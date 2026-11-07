#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include "wlr-virtual-pointer-client-protocol.h"

struct zwlr_virtual_pointer_manager_v1 *manager = NULL;
struct wl_seat *seat = NULL;

static void registry_handle_global(void *data, struct wl_registry *registry,
                                   uint32_t name, const char *interface, uint32_t version) {
    if (strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name) == 0) {
        manager = wl_registry_bind(registry, name, &zwlr_virtual_pointer_manager_v1_interface, 2);
    } else if (strcmp(interface, wl_seat_interface.name) == 0) {
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
    }
}

static void registry_handle_global_remove(void *data, struct wl_registry *registry, uint32_t name) {}

static const struct wl_registry_listener registry_listener = {
    .global = registry_handle_global,
    .global_remove = registry_handle_global_remove,
};

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <x> <y> [click (0 or 1)] [extent_w] [extent_h]\n", argv[0]);
        return 1;
    }
    uint32_t x = atoi(argv[1]);
    uint32_t y = atoi(argv[2]);
    int click = (argc > 3) ? atoi(argv[3]) : 0;
    uint32_t ext_w = (argc > 4) ? atoi(argv[4]) : 1280;
    uint32_t ext_h = (argc > 5) ? atoi(argv[5]) : 720;

    struct wl_display *display = wl_display_connect(NULL);
    if (!display) {
        fprintf(stderr, "Cannot connect to display\n");
        return 1;
    }

    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);

    if (!manager) {
        fprintf(stderr, "zwlr_virtual_pointer_manager_v1 not available\n");
        return 1;
    }

    struct zwlr_virtual_pointer_v1 *vp = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(manager, seat);
    wl_display_roundtrip(display);

    zwlr_virtual_pointer_v1_motion_absolute(vp, 0, x, y, ext_w, ext_h);
    zwlr_virtual_pointer_v1_frame(vp);
    wl_display_roundtrip(display);

    if (click) {
        zwlr_virtual_pointer_v1_button(vp, 0, 272, WL_POINTER_BUTTON_STATE_PRESSED);
        zwlr_virtual_pointer_v1_frame(vp);
        wl_display_roundtrip(display);
        usleep(50000);
        zwlr_virtual_pointer_v1_button(vp, 0, 272, WL_POINTER_BUTTON_STATE_RELEASED);
        zwlr_virtual_pointer_v1_frame(vp);
        wl_display_roundtrip(display);
    }

    zwlr_virtual_pointer_v1_destroy(vp);
    zwlr_virtual_pointer_manager_v1_destroy(manager);
    wl_display_disconnect(display);
    return 0;
}
