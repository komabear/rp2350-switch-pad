cmake_minimum_required(VERSION 3.13)

# use ../third_party/pico-sdk if present and no SDK path was given
if(NOT DEFINED PICO_SDK_PATH AND NOT DEFINED ENV{PICO_SDK_PATH}
   AND EXISTS ${CMAKE_CURRENT_LIST_DIR}/../third_party/pico-sdk)
    set(PICO_SDK_PATH ${CMAKE_CURRENT_LIST_DIR}/../third_party/pico-sdk)
endif()

if(NOT DEFINED PICO_PLATFORM)
    set(PICO_PLATFORM rp2350-arm-s)
endif()
if(NOT DEFINED PICO_BOARD)
    set(PICO_BOARD pico2)
endif()

include(pico_sdk_import.cmake)

project(mayflash_switch2_controller C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

# Pad presented to the Switch on the native port is the PowerA identity (3).
if(NOT DEFINED DEFAULT_IDENTITY)
    set(DEFAULT_IDENTITY 3)
endif()
add_compile_definitions(DEFAULT_IDENTITY=${DEFAULT_IDENTITY})

# native Type-C = pad for the Switch, PIO-USB Type-C = PC link (see README)
add_compile_definitions(NATIVE_PAD)

# -DSELFTEST=ON: board cycles the d-pad by itself (no PC needed)
option(SELFTEST "standalone d-pad test cycle" OFF)
if(SELFTEST)
    add_compile_definitions(SELFTEST)
endif()

# PIO-USB (device mode on the second Type-C port), vendored + patched
set(PIO_USB_DIR ${CMAKE_CURRENT_LIST_DIR}/pio_usb)

# One firmware image per Waveshare board. They differ only in the PIO-USB
# pins (fw/pio_usb/pio_usb_configuration.h):
#   bridge     RP2350-USB-C   PIO-USB Type-C socket, D+ = GPIO12, D- = GPIO13
#   bridge_cm  RP2350-USB-CM  PIO-USB Type-C plug,   D+ = GPIO13, D- = GPIO12
function(add_bridge_firmware TARGET USB_C USB_CM)
    add_executable(${TARGET})

    target_sources(${TARGET} PRIVATE
        main.c
        dinput_map.c
        switch_map.c
        pio_strings.c
        pio_identity.c
        ds4_map.c
        powa_map.c
        ds4_device.c
        usb_descriptors.c
    )

    pico_generate_pio_header(${TARGET} ${PIO_USB_DIR}/usb_tx.pio
        OUTPUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/${TARGET}_pio)
    pico_generate_pio_header(${TARGET} ${PIO_USB_DIR}/usb_rx.pio
        OUTPUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/${TARGET}_pio)
    target_sources(${TARGET} PRIVATE
        ${PIO_USB_DIR}/pio_usb.c
        ${PIO_USB_DIR}/pio_usb_device.c
        ${PIO_USB_DIR}/pio_usb_host.c
        ${PIO_USB_DIR}/usb_crc.c
    )
    target_include_directories(${TARGET} PRIVATE ${PIO_USB_DIR})

    # TinyUSB: native USB device (XInput + CDC)
    target_link_libraries(${TARGET} PRIVATE
        pico_stdlib
        pico_multicore
        pico_sync
        hardware_pio
        hardware_dma
        hardware_watchdog
        tinyusb_device
    )

    # let TinyUSB sources find tusb_config.h
    target_include_directories(${TARGET} PRIVATE ${CMAKE_CURRENT_LIST_DIR})

    target_compile_options(${TARGET} PRIVATE -Wall -Wextra)

    target_compile_definitions(${TARGET} PRIVATE
        RP2350_USB_C=${USB_C}
        RP2350_USB_CM=${USB_CM}
        # RP2350 scratch banks are 4 KB each; PIO-USB's core1 stack needs the room
        PICO_STACK_SIZE=0x1000
        PICO_CORE1_STACK_SIZE=0x1000
    )

    pico_enable_stdio_usb(${TARGET} 0)
    pico_enable_stdio_uart(${TARGET} 0)

    pico_add_extra_outputs(${TARGET})
endfunction()

add_bridge_firmware(bridge    1 0)   # bridge.uf2    - RP2350-USB-C
add_bridge_firmware(bridge_cm 0 1)   # bridge_cm.uf2 - RP2350-USB-CM
