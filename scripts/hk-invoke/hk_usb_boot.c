// Native macOS/libusb Harman Kardon Invoke usb_boot-compatible helper.
// Reverse-engineered from Harman OTA2 usb_boot enough to:
//   1. send bcm_erom.bin.usb to the Marvell iROM stage (VID:PID 1286:8174 subclass ff)
//   2. serve requested image files over bulk OUT endpoint 0x01
//   3. bridge the U-Boot console over interrupt endpoints 0x82/0x02
// It intentionally does NOT issue NAND-writing commands by itself.

#include <errno.h>
#include <fcntl.h>
#include <libusb.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define HK_VID 0x1286
#define HK_PID 0x8174
#define IFACE 0
#define EP_BULK_OUT 0x01
#define EP_BULK_IN  0x81
#define EP_INTR_OUT 0x02
#define EP_INTR_IN  0x82
#define CHUNK (1024 * 1024)
#define MARKER "i*m*g*r*q*"
#define MARKER_LEN 10

static volatile sig_atomic_t g_stop = 0;
static uint8_t g_last_img_type = 0;
static char g_console_history[256];
static size_t g_console_history_len = 0;
static bool g_console_suppress_until_eol = false;

static void on_sigint(int sig) {
    (void)sig;
    g_stop = 1;
}

static uint64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000ULL + (uint64_t)tv.tv_usec / 1000ULL;
}

static const char *usb_err(int rc) {
    return libusb_error_name(rc);
}

static int ensure_dir_slash(const char *dir, char *out, size_t outsz) {
    size_t n = strlen(dir);
    if (n + 2 > outsz) return -1;
    strcpy(out, dir);
    if (n == 0 || out[n - 1] != '/') {
        out[n] = '/';
        out[n + 1] = '\0';
    }
    return 0;
}

static int path_join(const char *dir, const char *name, char *out, size_t outsz) {
    char prefix[4096];
    if (ensure_dir_slash(dir, prefix, sizeof(prefix)) != 0) return -1;
    if (strlen(prefix) + strlen(name) + 1 > outsz) return -1;
    strcpy(out, prefix);
    strcat(out, name);
    return 0;
}

static int file_size_u32(const char *path, uint32_t *size_out) {
    struct stat st;
    if (stat(path, &st) != 0) {
        fprintf(stderr, "stat failed for %s: %s\n", path, strerror(errno));
        return -1;
    }
    if (st.st_size < 0 || (uint64_t)st.st_size > 0xffffffffULL) {
        fprintf(stderr, "unsupported file size for %s: %lld\n", path, (long long)st.st_size);
        return -1;
    }
    *size_out = (uint32_t)st.st_size;
    return 0;
}

static void print_console_sanitized(const uint8_t *buf, int len) {
    if (len <= 0) return;
    for (int i = 0; i < len; i++) {
        uint8_t c = buf[i];
        if (c == '\r' || c == '\n' || c == '\t' || (c >= 0x20 && c <= 0x7e)) {
            fputc((int)c, stdout);
        } else if (c != 0x00) {
            fprintf(stdout, "<%02x>", c);
        }
    }
    fflush(stdout);
}

static bool byte_is_console_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static unsigned char byte_to_lower(unsigned char c) {
    if (c >= 'A' && c <= 'Z') return (unsigned char)(c - 'A' + 'a');
    return c;
}

static void reset_console_history(void) {
    g_console_history_len = 0;
    g_console_history[0] = '\0';
}

static void append_console_history(const char *buf, size_t len) {
    if (len == 0) return;
    if (len >= sizeof(g_console_history)) {
        buf += len - (sizeof(g_console_history) - 1);
        len = sizeof(g_console_history) - 1;
        g_console_history_len = 0;
    } else if (g_console_history_len + len >= sizeof(g_console_history)) {
        size_t drop = g_console_history_len + len - (sizeof(g_console_history) - 1);
        memmove(g_console_history, g_console_history + drop, g_console_history_len - drop);
        g_console_history_len -= drop;
    }
    memcpy(g_console_history + g_console_history_len, buf, len);
    g_console_history_len += len;
    g_console_history[g_console_history_len] = '\0';
}

static bool buffer_contains_eol(const char *buf, size_t len) {
    for (size_t i = 0; i < len; i++) {
        if (buf[i] == '\r' || buf[i] == '\n') return true;
    }
    return false;
}

static bool normalize_console_window(const char *history,
                                     size_t history_len,
                                     const char *buf,
                                     size_t len,
                                     char *out,
                                     size_t outsz) {
    if (outsz == 0) return false;
    size_t j = 0;
    bool last_space = true;
    for (int pass = 0; pass < 2; pass++) {
        const char *src = pass == 0 ? history : buf;
        size_t src_len = pass == 0 ? history_len : len;
        for (size_t i = 0; i < src_len; i++) {
            unsigned char c = (unsigned char)src[i];
            if (byte_is_console_space(c)) {
                if (!last_space && j + 1 < outsz) {
                    out[j++] = ' ';
                    last_space = true;
                }
                continue;
            }
            if (c < 0x20 || c > 0x7e) continue;
            if (j + 1 >= outsz) return false;
            out[j++] = (char)byte_to_lower(c);
            last_space = false;
        }
    }
    while (j > 0 && out[j - 1] == ' ') j--;
    out[j] = '\0';
    return true;
}

static bool console_input_would_send_forbidden(const char *buf,
                                               size_t len,
                                               char *match,
                                               size_t match_len) {
    char normalized[768];
    const char *needles[] = {
        "l2nand",
        "tftp2nand",
        "usb2nand",
        "tftp2emmc",
        "usb2emmc",
        "nanderase",
        "nandinit",
        "nandmarkbad",
        "nandverify",
        "nandwr",
        "nand write",
        "nand erase",
        "mmc write",
        "run upgrade",
        "protect",
        "erase",
        "saveenv",
    };

    if (!normalize_console_window(g_console_history,
                                  g_console_history_len,
                                  buf,
                                  len,
                                  normalized,
                                  sizeof(normalized))) {
        snprintf(match, match_len, "oversized-console-command");
        return true;
    }

    for (size_t i = 0; i < sizeof(needles) / sizeof(needles[0]); i++) {
        if (strstr(normalized, needles[i]) != NULL) {
            snprintf(match, match_len, "%s", needles[i]);
            return true;
        }
    }
    return false;
}

static int safety_self_test(void) {
    char match[64];

    reset_console_history();
    if (console_input_would_send_forbidden("printenv\n", 9, match, sizeof(match))) return 10;
    append_console_history("printenv\n", 9);

    reset_console_history();
    if (!console_input_would_send_forbidden("nand write 0x0 0x0 0x1\n", 23, match, sizeof(match))) return 11;

    reset_console_history();
    if (console_input_would_send_forbidden("nand ", 5, match, sizeof(match))) return 12;
    append_console_history("nand ", 5);
    if (!console_input_would_send_forbidden("erase\n", 6, match, sizeof(match))) return 13;

    reset_console_history();
    if (console_input_would_send_forbidden("saveen", 6, match, sizeof(match))) return 14;
    append_console_history("saveen", 6);
    if (!console_input_would_send_forbidden("v\n", 2, match, sizeof(match))) return 15;

    reset_console_history();
    if (!console_input_would_send_forbidden("tftp2nand 83_IMAGE\n", 18, match, sizeof(match))) return 16;
    if (!console_input_would_send_forbidden("l2nand 83\n", 10, match, sizeof(match))) return 17;
    if (!console_input_would_send_forbidden("nanderase\n", 10, match, sizeof(match))) return 18;
    if (!console_input_would_send_forbidden("usb2nand\n", 9, match, sizeof(match))) return 19;
    if (!console_input_would_send_forbidden("usb2emmc\n", 9, match, sizeof(match))) return 20;
    if (!console_input_would_send_forbidden("nandmarkbad 0\n", 14, match, sizeof(match))) return 21;
    if (!console_input_would_send_forbidden("nandverify\n", 11, match, sizeof(match))) return 22;
    if (!console_input_would_send_forbidden("protect off all\n", 16, match, sizeof(match))) return 23;
    if (!console_input_would_send_forbidden("erase f0000000 f00fffff\n", 23, match, sizeof(match))) return 24;
    if (!console_input_would_send_forbidden("run upgrade\n", 12, match, sizeof(match))) return 25;

    fprintf(stderr, "safety self-test passed\n");
    return 0;
}

static int check_image_dir_safety(const char *image_dir) {
    char path[4096];
    if (path_join(image_dir, "79_IMAGE", path, sizeof(path)) != 0) {
        fprintf(stderr, "cannot build 79_IMAGE path for safety scan\n");
        return 2;
    }

    FILE *fp = fopen(path, "rb");
    if (!fp) {
        fprintf(stderr, "open failed for safety scan %s: %s\n", path, strerror(errno));
        return 2;
    }

    reset_console_history();
    char buf[512];
    char match[64];
    while (!feof(fp)) {
        size_t n = fread(buf, 1, sizeof(buf), fp);
        if (n > 0) {
            if (console_input_would_send_forbidden(buf, n, match, sizeof(match))) {
                fclose(fp);
                reset_console_history();
                fprintf(stderr, "ERROR: refusing %s; forbidden persistent/NAND command found (%s)\n", path, match);
                return 3;
            }
            append_console_history(buf, n);
            if (buffer_contains_eol(buf, n)) reset_console_history();
        }
        if (ferror(fp)) {
            fprintf(stderr, "read failed during safety scan %s\n", path);
            fclose(fp);
            reset_console_history();
            return 2;
        }
    }
    fclose(fp);
    reset_console_history();
    fprintf(stderr, "image safety scan passed: %s\n", path);
    return 0;
}

static libusb_device_handle *open_matching(libusb_context *ctx, uint8_t *class_out, uint8_t *subclass_out, uint8_t *proto_out) {
    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx, &list);
    if (n < 0) {
        fprintf(stderr, "libusb_get_device_list failed: %s\n", usb_err((int)n));
        return NULL;
    }

    libusb_device_handle *handle = NULL;
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        int rc = libusb_get_device_descriptor(list[i], &d);
        if (rc != 0) continue;
        if (d.idVendor == HK_VID && d.idProduct == HK_PID) {
            rc = libusb_open(list[i], &handle);
            if (rc != 0) {
                fprintf(stderr, "found %04x:%04x but open failed: %s\n", HK_VID, HK_PID, usb_err(rc));
                handle = NULL;
                continue;
            }
            if (class_out) *class_out = d.bDeviceClass;
            if (subclass_out) *subclass_out = d.bDeviceSubClass;
            if (proto_out) *proto_out = d.bDeviceProtocol;
            break;
        }
    }
    libusb_free_device_list(list, 1);
    return handle;
}

static int claim_iface(libusb_device_handle *h) {
    // Do not call libusb_set_auto_detach_kernel_driver on macOS: it emits
    // entitlement/root warnings for this vendor-class interface, while a direct
    // claim works for the Invoke.
    int rc = libusb_claim_interface(h, IFACE);
    if (rc != 0) {
        fprintf(stderr, "claim interface %d failed: %s\n", IFACE, usb_err(rc));
        return rc;
    }
    return 0;
}

static void release_iface(libusb_device_handle *h) {
    if (h) libusb_release_interface(h, IFACE);
}

static int detect(libusb_context *ctx) {
    libusb_device **list = NULL;
    ssize_t n = libusb_get_device_list(ctx, &list);
    if (n < 0) {
        fprintf(stderr, "libusb_get_device_list failed: %s\n", usb_err((int)n));
        return 2;
    }
    int matches = 0;
    printf("libusb devices matching %04x:%04x\n", HK_VID, HK_PID);
    for (ssize_t i = 0; i < n; i++) {
        struct libusb_device_descriptor d;
        int rc = libusb_get_device_descriptor(list[i], &d);
        if (rc != 0) continue;
        if (d.idVendor != HK_VID || d.idProduct != HK_PID) continue;
        matches++;
        printf("- bus=%03u addr=%03u port=%u class=%02x subclass=%02x proto=%02x configs=%u\n",
               libusb_get_bus_number(list[i]), libusb_get_device_address(list[i]),
               libusb_get_port_number(list[i]), d.bDeviceClass, d.bDeviceSubClass,
               d.bDeviceProtocol, d.bNumConfigurations);
        for (uint8_t ci = 0; ci < d.bNumConfigurations; ci++) {
            struct libusb_config_descriptor *cfg = NULL;
            rc = libusb_get_config_descriptor(list[i], ci, &cfg);
            if (rc != 0) {
                printf("  config %u descriptor failed: %s\n", ci, usb_err(rc));
                continue;
            }
            printf("  config[%u] value=%u interfaces=%u attr=0x%02x maxpower=%umA\n",
                   ci, cfg->bConfigurationValue, cfg->bNumInterfaces,
                   cfg->bmAttributes, cfg->MaxPower * 2);
            for (uint8_t ifi = 0; ifi < cfg->bNumInterfaces; ifi++) {
                const struct libusb_interface *iface = &cfg->interface[ifi];
                for (int ai = 0; ai < iface->num_altsetting; ai++) {
                    const struct libusb_interface_descriptor *alt = &iface->altsetting[ai];
                    printf("    if=%u alt=%u class=%02x subclass=%02x proto=%02x eps=%u\n",
                           alt->bInterfaceNumber, alt->bAlternateSetting,
                           alt->bInterfaceClass, alt->bInterfaceSubClass,
                           alt->bInterfaceProtocol, alt->bNumEndpoints);
                    for (uint8_t ei = 0; ei < alt->bNumEndpoints; ei++) {
                        const struct libusb_endpoint_descriptor *ep = &alt->endpoint[ei];
                        printf("      ep=0x%02x attr=0x%02x maxpkt=%u interval=%u\n",
                               ep->bEndpointAddress, ep->bmAttributes,
                               ep->wMaxPacketSize, ep->bInterval);
                    }
                }
            }
            libusb_free_config_descriptor(cfg);
        }
    }
    libusb_free_device_list(list, 1);
    if (!matches) {
        printf("no Invoke/Marvell WTP device currently visible\n");
        return 1;
    }
    return 0;
}

static int bulk_write_all(libusb_device_handle *h, const uint8_t *buf, int len, unsigned int timeout_ms) {
    int off = 0;
    int timeout_retries = 0;
    while (off < len) {
        int xfer = 0;
        int rc = libusb_bulk_transfer(h, EP_BULK_OUT, (unsigned char *)buf + off, len - off, &xfer, timeout_ms);
        if (xfer > 0) {
            off += xfer;
            timeout_retries = 0;
            continue;
        }
        if (rc == LIBUSB_ERROR_TIMEOUT && timeout_retries < 5) {
            timeout_retries++;
            fprintf(stderr,
                    "bulk OUT timeout after %d/%d bytes; retry %d/5\n",
                    off,
                    len,
                    timeout_retries);
            usleep(200 * 1000);
            continue;
        }
        if (rc != 0) {
            fprintf(stderr, "bulk OUT failed after %d/%d bytes: %s\n", off, len, usb_err(rc));
            return rc;
        }
        fprintf(stderr, "bulk OUT made no progress after %d/%d bytes\n", off, len);
        return LIBUSB_ERROR_IO;
    }
    return 0;
}

static int send_file_raw(libusb_device_handle *h, const char *path, bool header) {
    uint32_t sz = 0;
    if (file_size_u32(path, &sz) != 0) return -1;
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        fprintf(stderr, "open failed for %s: %s\n", path, strerror(errno));
        return -1;
    }

    fprintf(stderr, "sending %s (%u bytes)%s\n", path, sz, header ? " with 8-byte size header" : " raw");
    if (header) {
        uint8_t hdr[8] = {0};
        hdr[0] = (uint8_t)(sz & 0xff);
        hdr[1] = (uint8_t)((sz >> 8) & 0xff);
        hdr[2] = (uint8_t)((sz >> 16) & 0xff);
        hdr[3] = (uint8_t)((sz >> 24) & 0xff);
        int rc = bulk_write_all(h, hdr, 8, 10000);
        if (rc != 0) {
            fclose(fp);
            return rc;
        }
    }

    uint8_t *buf = (uint8_t *)malloc(CHUNK);
    if (!buf) {
        fclose(fp);
        fprintf(stderr, "malloc failed\n");
        return -1;
    }
    uint32_t sent = 0;
    uint32_t next_report = 4 * 1024 * 1024;
    while (!g_stop) {
        size_t n = fread(buf, 1, CHUNK, fp);
        if (n > 0) {
            int rc = bulk_write_all(h, buf, (int)n, 30000);
            if (rc != 0) {
                free(buf);
                fclose(fp);
                return rc;
            }
            sent += (uint32_t)n;
            if (sent >= next_report || sent == sz) {
                fprintf(stderr, "  sent %u/%u bytes (%.1f%%)\n", sent, sz, sz ? (100.0 * (double)sent / (double)sz) : 100.0);
                while (next_report <= sent) next_report += 4 * 1024 * 1024;
            }
        }
        if (n < CHUNK) {
            if (ferror(fp)) {
                fprintf(stderr, "read failed for %s\n", path);
                free(buf);
                fclose(fp);
                return -1;
            }
            break;
        }
    }
    free(buf);
    fclose(fp);
    if (sent != sz) {
        fprintf(stderr,
                "partial transfer for %s: sent %u/%u bytes%s\n",
                path,
                sent,
                sz,
                g_stop ? " (interrupted)" : "");
        return g_stop ? 130 : LIBUSB_ERROR_IO;
    }
    fprintf(stderr, "done sending %s\n", path);
    return 0;
}

static int write_07_size_side_effect(const char *image_dir, uint32_t sz) {
    char p[4096];
    if (path_join(image_dir, "07_IMAGE", p, sizeof(p)) != 0) {
        fprintf(stderr, "cannot build 07_IMAGE path\n");
        return -1;
    }
    FILE *fp = fopen(p, "wb");
    if (!fp) {
        fprintf(stderr, "warning: cannot update %s: %s\n", p, strerror(errno));
        return -1;
    }
    uint8_t b[4] = {
        (uint8_t)(sz & 0xff),
        (uint8_t)((sz >> 8) & 0xff),
        (uint8_t)((sz >> 16) & 0xff),
        (uint8_t)((sz >> 24) & 0xff),
    };
    size_t n = fwrite(b, 1, sizeof(b), fp);
    fclose(fp);
    if (n != sizeof(b)) {
        fprintf(stderr, "warning: short write updating %s\n", p);
        return -1;
    }
    fprintf(stderr, "updated 07_IMAGE side-effect with size %u for requested type 0x%02x\n", sz, g_last_img_type);
    return 0;
}

static int image_path_for_type(const char *image_dir, uint8_t t, char *out, size_t outsz) {
    const char *name = NULL;
    char generated[64];
    if (t == 0x01) return 1; // original does no transfer for type 1
    if (t == 0x02) name = "sysinit.img";
    else if (t == 0x03) name = "bootloader.img";
    else if (t == 0x05) name = "drm_erom.img";
    else {
        snprintf(generated, sizeof(generated), "%02x_IMAGE", t);
        name = generated;
    }
    if (path_join(image_dir, name, out, outsz) != 0) return -1;
    return 0;
}

static int serve_request(libusb_device_handle *h, const char *image_dir, uint8_t t) {
    g_last_img_type = t;
    char path[4096];
    int m = image_path_for_type(image_dir, t, path, sizeof(path));
    if (m == 1) {
        fprintf(stderr, "request type 0x%02x: no image transfer per original usb_boot\n", t);
        return 0;
    }
    if (m != 0) {
        fprintf(stderr, "request type 0x%02x: cannot construct image path\n", t);
        return -1;
    }
    uint32_t sz = 0;
    if (file_size_u32(path, &sz) != 0) return -1;
    if (t > 0x79) (void)write_07_size_side_effect(image_dir, sz);
    fprintf(stderr, "image request type=0x%02x -> %s (%u bytes)\n", t, path, sz);
    return send_file_raw(h, path, true);
}

static int push_file_to_waiting_usbload(libusb_context *ctx, const char *path) {
    uint8_t cls = 0, sub = 0, proto = 0;
    uint32_t sz = 0;
    if (file_size_u32(path, &sz) != 0) return -1;

    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    if (strcmp(base, "79_IMAGE") == 0) {
        fprintf(stderr, "push-file: refusing 79_IMAGE command script; use guarded serve/check-images path instead\n");
        return 3;
    }
    if (strncmp(base, "81_IMAGE", 8) != 0 && strncmp(base, "82_IMAGE", 8) != 0) {
        fprintf(stderr,
                "push-file: refusing %s; only 81_IMAGE* or 82_IMAGE* RAM payloads are allowed\n",
                base);
        return 4;
    }

    libusb_device_handle *h = open_matching(ctx, &cls, &sub, &proto);
    if (!h) {
        fprintf(stderr, "push-file: device not openable\n");
        return 1;
    }
    fprintf(stderr, "opened %04x:%04x class=%02x subclass=%02x proto=%02x for push-file\n",
            HK_VID,
            HK_PID,
            cls,
            sub,
            proto);
    if (sub == 0xff) {
        fprintf(stderr, "push-file: device is still iROM subclass ff; refusing bulk image push\n");
        libusb_close(h);
        return 2;
    }
    int rc = claim_iface(h);
    if (rc != 0) {
        libusb_close(h);
        return rc;
    }
    fprintf(stderr, "push-file: sending %u-byte file to pending usbload receiver; RAM-only host transfer\n", sz);
    rc = send_file_raw(h, path, true);
    release_iface(h);
    libusb_close(h);
    return rc;
}

static int send_stage1(libusb_context *ctx, const char *image_dir) {
    uint8_t cls = 0, sub = 0, proto = 0;
    libusb_device_handle *h = open_matching(ctx, &cls, &sub, &proto);
    if (!h) {
        fprintf(stderr, "no %04x:%04x device openable for stage1\n", HK_VID, HK_PID);
        return 1;
    }
    fprintf(stderr, "opened %04x:%04x class=%02x subclass=%02x proto=%02x\n", HK_VID, HK_PID, cls, sub, proto);
    if (sub != 0xff) {
        fprintf(stderr, "device is not iROM subclass ff; refusing stage1 raw bcm_erom send\n");
        libusb_close(h);
        return 2;
    }
    int rc = claim_iface(h);
    if (rc != 0) {
        libusb_close(h);
        return rc;
    }
    char path[4096];
    if (path_join(image_dir, "bcm_erom.bin.usb", path, sizeof(path)) != 0) {
        fprintf(stderr, "cannot build bcm_erom.bin.usb path\n");
        release_iface(h);
        libusb_close(h);
        return -1;
    }
    rc = send_file_raw(h, path, false);
    release_iface(h);
    libusb_close(h);
    return rc;
}

static libusb_device_handle *wait_for_device(libusb_context *ctx, bool want_irom, int timeout_ms) {
    uint64_t deadline = now_ms() + (uint64_t)timeout_ms;
    while (!g_stop && now_ms() < deadline) {
        uint8_t cls = 0, sub = 0, proto = 0;
        libusb_device_handle *h = open_matching(ctx, &cls, &sub, &proto);
        if (h) {
            bool is_irom = (sub == 0xff);
            if (is_irom == want_irom) {
                fprintf(stderr, "device ready class=%02x subclass=%02x proto=%02x (%s)\n", cls, sub, proto, is_irom ? "iROM" : "normal") ;
                return h;
            }
            libusb_close(h);
        }
        usleep(200 * 1000);
    }
    fprintf(stderr, "timeout waiting for %s device\n", want_irom ? "iROM" : "normal/re-enumerated");
    return NULL;
}

static int make_stdin_nonblocking(void) {
    int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    if (flags < 0) return -1;
    if (fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) return -1;
    return 0;
}

static void poll_stdin_and_send(libusb_device_handle *h, bool *stdin_closed) {
    if (*stdin_closed) return;
    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(STDIN_FILENO, &rfds);
    struct timeval tv = {0, 0};
    int rc = select(STDIN_FILENO + 1, &rfds, NULL, NULL, &tv);
    if (rc <= 0 || !FD_ISSET(STDIN_FILENO, &rfds)) return;

    char line[512];
    ssize_t n = read(STDIN_FILENO, line, sizeof(line));
    if (n == 0) {
        *stdin_closed = true;
        return;
    }
    if (n < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK) {
            fprintf(stderr, "stdin read error: %s\n", strerror(errno));
            *stdin_closed = true;
        }
        return;
    }
    if (n > 0) {
        if (g_console_suppress_until_eol) {
            if (buffer_contains_eol(line, (size_t)n)) {
                g_console_suppress_until_eol = false;
                reset_console_history();
            }
            fprintf(stderr, "\n[usb console safety: suppressed %zd byte(s) from blocked line]\n", n);
            return;
        }

        char match[64];
        if (console_input_would_send_forbidden(line, (size_t)n, match, sizeof(match))) {
            fprintf(stderr,
                    "\nBLOCKED: persistent/NAND-affecting U-Boot command refused by hk_usb_boot safety filter (%s).\n",
                    match);
            fprintf(stderr, "The rest of this input line will be suppressed; press Enter before typing a safe command.\n");
            g_console_suppress_until_eol = !buffer_contains_eol(line, (size_t)n);
            reset_console_history();
            return;
        }

        int xfer = 0;
        rc = libusb_interrupt_transfer(h, EP_INTR_OUT, (unsigned char *)line, (int)n, &xfer, 5000);
        if (rc != 0) fprintf(stderr, "interrupt OUT command write failed: %s\n", usb_err(rc));
        else {
            append_console_history(line, (size_t)xfer);
            if (buffer_contains_eol(line, (size_t)xfer)) reset_console_history();
            fprintf(stderr, "\n[usb console <- wrote %d bytes]\n", xfer);
        }
    }
}

static int serve_loop(libusb_context *ctx, const char *image_dir) {
    int safety = check_image_dir_safety(image_dir);
    if (safety != 0) return safety;

    uint8_t cls = 0, sub = 0, proto = 0;
    libusb_device_handle *h = open_matching(ctx, &cls, &sub, &proto);
    if (!h) {
        fprintf(stderr, "normal serve: device not openable; waiting up to 60s\n");
        h = wait_for_device(ctx, false, 60000);
        if (!h) return 1;
        // Refresh descriptor state for logging.
        libusb_close(h);
        h = open_matching(ctx, &cls, &sub, &proto);
        if (!h) return 1;
    }
    fprintf(stderr, "opened %04x:%04x class=%02x subclass=%02x proto=%02x for serve\n", HK_VID, HK_PID, cls, sub, proto);
    if (sub == 0xff) {
        fprintf(stderr, "device still looks like iROM subclass ff; run auto/stage1 first\n");
        libusb_close(h);
        return 2;
    }
    int rc = claim_iface(h);
    if (rc != 0) {
        libusb_close(h);
        return rc;
    }

    (void)make_stdin_nonblocking();
    bool stdin_closed = false;
    fprintf(stderr, "serving image requests from %s\n", image_dir);
    fprintf(stderr, "interactive console ready: type U-Boot commands here; Ctrl-C exits.\n");
    fprintf(stderr, "Safety filter blocks NAND/eMMC/SPI/env write commands before USB OUT.\n");

    uint8_t ibuf[1024];
    uint8_t bbuf[1024];
    uint8_t tail[MARKER_LEN];
    int tail_len = 0;

    while (!g_stop) {
        int xfer = 0;
        rc = libusb_interrupt_transfer(h, EP_INTR_IN, ibuf, sizeof(ibuf), &xfer, 100);
        if (rc == 0 && xfer > 0) {
            // Parse only newly-arrived request markers, while keeping enough tail
            // to catch a marker split across interrupt packets. The first bridge
            // version reparsed the whole rolling buffer and incorrectly answered
            // the same request multiple times.
            uint8_t combo[MARKER_LEN + sizeof(ibuf)];
            memcpy(combo, tail, tail_len);
            memcpy(combo + tail_len, ibuf, xfer);
            int combo_len = tail_len + xfer;
            for (int i = 0; i <= combo_len - MARKER_LEN - 1; i++) {
                if (i + MARKER_LEN < tail_len) continue; // marker+type were wholly old
                if (memcmp(combo + i, MARKER, MARKER_LEN) == 0) {
                    uint8_t t = combo[i + MARKER_LEN];
                    fprintf(stderr, "\nreceived image request marker, type=0x%02x\n", t);
                    int sreq = serve_request(h, image_dir, t);
                    if (sreq != 0) fprintf(stderr, "serve request type=0x%02x failed rc=%d\n", t, sreq);
                    i += MARKER_LEN;
                }
            }
            tail_len = combo_len < MARKER_LEN ? combo_len : MARKER_LEN;
            memcpy(tail, combo + combo_len - tail_len, tail_len);
            print_console_sanitized(ibuf, xfer);
        } else if (rc != 0 && rc != LIBUSB_ERROR_TIMEOUT) {
            fprintf(stderr, "interrupt IN read: %s\n", usb_err(rc));
            if (rc == LIBUSB_ERROR_NO_DEVICE) break;
            usleep(100 * 1000);
        }

        xfer = 0;
        rc = libusb_bulk_transfer(h, EP_BULK_IN, bbuf, sizeof(bbuf), &xfer, 10);
        if (rc == 0 && xfer > 0) {
            print_console_sanitized(bbuf, xfer);
        } else if (rc != 0 && rc != LIBUSB_ERROR_TIMEOUT) {
            // Bulk IN can be quiet; report real errors but keep going unless disconnected.
            fprintf(stderr, "bulk IN read: %s\n", usb_err(rc));
            if (rc == LIBUSB_ERROR_NO_DEVICE) break;
        }

        poll_stdin_and_send(h, &stdin_closed);
    }

    fprintf(stderr, "\nserve loop ending\n");
    release_iface(h);
    libusb_close(h);
    return g_stop ? 130 : 0;
}

static int auto_mode(libusb_context *ctx, const char *image_dir) {
    int safety = check_image_dir_safety(image_dir);
    if (safety != 0) return safety;

    int cycles = 0;
    while (!g_stop) {
        cycles++;
        uint8_t cls = 0, sub = 0, proto = 0;
        libusb_device_handle *h = open_matching(ctx, &cls, &sub, &proto);
        if (!h) {
            fprintf(stderr, "auto[%d]: no device visible; waiting for Invoke Marvell USB device\n", cycles);
            uint64_t deadline = now_ms() + 120000ULL;
            while (!g_stop && now_ms() < deadline) {
                h = open_matching(ctx, &cls, &sub, &proto);
                if (h) break;
                usleep(200 * 1000);
            }
            if (!h) return 1;
        }
        libusb_close(h);

        if (sub == 0xff) {
            fprintf(stderr, "auto[%d]: iROM stage detected; sending bcm_erom.bin.usb\n", cycles);
            int rc = send_stage1(ctx, image_dir);
            if (rc != 0) {
                fprintf(stderr, "stage1 send failed rc=%d\n", rc);
                return rc;
            }
            fprintf(stderr, "auto[%d]: stage1 sent; waiting for re-enumerated normal device\n", cycles);
            libusb_device_handle *nh = wait_for_device(ctx, false, 60000);
            if (!nh) {
                fprintf(stderr, "auto[%d]: no normal device after stage1; will continue watching\n", cycles);
                continue;
            }
            libusb_close(nh);
        } else {
            fprintf(stderr, "auto[%d]: device appears past iROM (subclass=%02x); starting serve\n", cycles, sub);
        }

        int rc = serve_loop(ctx, image_dir);
        if (g_stop || rc == 130) return rc;
        fprintf(stderr, "auto[%d]: serve ended rc=%d; waiting for next re-enumeration\n", cycles, rc);
        usleep(500 * 1000);
    }
    return 130;
}

static void usage(const char *argv0) {
    fprintf(stderr,
            "usage:\n"
            "  %s detect\n"
            "  %s safety-self-test\n"
            "  %s check-images <OTA2-image-dir>\n"
            "  %s stage1 <OTA2-image-dir>\n"
            "  %s push-file <image-file>\n"
            "  %s serve  <OTA2-image-dir>\n"
            "  %s auto   <OTA2-image-dir>\n"
            "\n"
            "Use a writable work copy of OTA2 as <OTA2-image-dir>; usb_boot mutates 07_IMAGE for >0x79 requests.\n",
            argv0, argv0, argv0, argv0, argv0, argv0, argv0);
}

int main(int argc, char **argv) {
    signal(SIGINT, on_sigint);
    signal(SIGTERM, on_sigint);

    if (argc < 2) {
        usage(argv[0]);
        return 2;
    }

    if (strcmp(argv[1], "safety-self-test") == 0) {
        return safety_self_test();
    }

    if (strcmp(argv[1], "check-images") == 0) {
        if (argc < 3) {
            usage(argv[0]);
            return 2;
        }
        return check_image_dir_safety(argv[2]);
    }

    libusb_context *ctx = NULL;
    int rc = libusb_init(&ctx);
    if (rc != 0) {
        fprintf(stderr, "libusb_init failed: %s\n", usb_err(rc));
        return 2;
    }
    libusb_set_option(ctx, LIBUSB_OPTION_LOG_LEVEL, LIBUSB_LOG_LEVEL_ERROR);

    const char *mode = argv[1];
    int exit_code = 0;
    if (strcmp(mode, "detect") == 0) {
        exit_code = detect(ctx);
    } else if (strcmp(mode, "stage1") == 0) {
        if (argc < 3) { usage(argv[0]); exit_code = 2; }
        else exit_code = send_stage1(ctx, argv[2]);
    } else if (strcmp(mode, "push-file") == 0) {
        if (argc < 3) { usage(argv[0]); exit_code = 2; }
        else exit_code = push_file_to_waiting_usbload(ctx, argv[2]);
    } else if (strcmp(mode, "serve") == 0) {
        if (argc < 3) { usage(argv[0]); exit_code = 2; }
        else exit_code = serve_loop(ctx, argv[2]);
    } else if (strcmp(mode, "auto") == 0) {
        if (argc < 3) { usage(argv[0]); exit_code = 2; }
        else exit_code = auto_mode(ctx, argv[2]);
    } else {
        usage(argv[0]);
        exit_code = 2;
    }

    libusb_exit(ctx);
    return exit_code;
}
