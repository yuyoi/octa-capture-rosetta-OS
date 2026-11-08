/* lab build config: values from the USB-MIDI RE (task 9 dispatcher 0x369468, TX 0x36bcf4). */
#define LAB_VER 0x0107
#define UPLOAD_BASE 0x003B0000u     /* dead init-table area, executable (ICPLB1), free after meminit */
/* 0x36bcf4(R0 = USB-MIDI packet word: byte0 = cable<<4|CIN, bytes 1-3 = MIDI). Returns 0 if USB-MIDI is off. */
#define usb_midi_tx(w) (((int (*)(unsigned))0x0036bcf4u)(w))
