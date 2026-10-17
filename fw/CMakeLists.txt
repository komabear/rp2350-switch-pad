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

add_executable(bridge)

target_sources(bridge PRIVATE
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

# PIO-USB (device mode on the second Type-C port), vendored + patched
set(PIO_USB_DIR ${CMAKE_CURRENT_LIST_DIR}/pio_usb)
pico_generate_pio_header(bridge ${PIO_USB_DIR}/usb_tx.pio)
pico_generate_pio_header(bridge ${PIO_USB_DIR}/usb_rx.pio)
target_sources(bridge PRIVATE
    ${PIO_USB_DIR}/pio_usb.c
    ${PIO_USB_DIR}/pio_usb_device.c
    ${PIO_USB_DIR}/pio_usb_host.c
    ${PIO_USB_DIR}/usb_crc.c
)
target_include_directories(bridge PRIVATE ${PIO_USB_DIR})

# TinyUSB: native USB device (XInput + CDC)
target_link_libraries(bridge PRIVATE
    pico_stdlib
    pico_multicore
    pico_sync
    hardware_pio
    hardware_dma
    hardware_watchdog
    tinyusb_device
)

# let TinyUSB sources find tusb_config.h
target_include_directories(bridge PRIVATE ${CMAKE_CURRENT_LIST_DIR})

target_compile_options(bridge PRIVATE -Wall -Wextra)

# RP2350 scratch banks are 4 KB each; PIO-USB's core1 stack needs the room
target_compile_definitions(bridge PRIVATE
    PICO_STACK_SIZE=0x1000
    PICO_CORE1_STACK_SIZE=0x1000
)

pico_enable_stdio_usb(bridge 0)
pico_enable_stdio_uart(bridge 0)

pico_add_extra_outputs(bridge)
