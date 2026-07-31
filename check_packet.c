/*
 * check_packet.c — Print a packet as hex and compare with captured frames
 *
 * Compile: gcc -o check_packet check_packet.c
 * Run:     ./check_packet
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#define SCALE       10
#define PACKET_SIZE 45



static int16_t kg_to_counts(double kg) {
    long c = lround(kg * SCALE);
    if (c >  32767) c =  32767;
    if (c < -32768) c = -32768;
    return (int16_t)c;
}



static void write_le16(uint8_t *buf, int16_t val)  

    buf[0] = (uint8_t)(val & 0xFF);
    buf[1] = (uint8_t)((val >> 8) & 0xFF);
}



static void build_group(uint8_t *dst, uint8_t marker, int16_t counts) {
    dst[0] = marker;
    write_le16(dst + 1, 0);
    write_le16(dst + 3, counts);
    write_le16(dst + 5, counts);
    write_le16(dst + 7, counts);
    write_le16(dst + 9, counts);
}



static void build_packet(double weight_kg, uint8_t *pkt) {
    int16_t counts = kg_to_counts(weight_kg);
    pkt[0] = 0x80;
    build_group(pkt +  1, 0xC0, counts);
    build_group(pkt + 12, 0xC1, counts);
    build_group(pkt + 23, 0xC2, counts);
    build_group(pkt + 34, 0xC3, counts);
}



static void print_hex(const char *label, const uint8_t *data, int len) {
    printf("%s\n  ", label);
    for (int i = 0; i < len; i++) {
        printf("%02x ", data[i]);
        if ((i + 1) % 13 == 0 && i + 1 < len) printf("\n  ");
    }
    printf("\n\n");
}



/* g points to marker byte, data follows: skip(2) P1(2) P2(2) P3(2) P4(2) */
static void decode_group(const uint8_t *g) {
    int16_t skip = (int16_t)(g[1] | (g[2] << 8));
    int16_t p1   = (int16_t)(g[3] | (g[4] << 8));
    int16_t p2   = (int16_t)(g[5] | (g[6] << 8));
    int16_t p3   = (int16_t)(g[7] | (g[8] << 8));
    int16_t p4   = (int16_t)(g[9] | (g[10] << 8));
    printf("    marker=%02x  skip=%-6d  P1=%-6d  P2=%-6d  P3=%-6d  P4=%-6d\n",
           g[0], skip, p1, p2, p3, p4);
}



static void decode_packet(const char *label, const uint8_t *pkt) {
    printf("%s decoded:\n", label);
    printf("  Header : %02x\n", pkt[0]);
    printf("  Group1 :"); decode_group(pkt +  1);
    printf("  Group2 :"); decode_group(pkt + 12);
    printf("  Group3 :"); decode_group(pkt + 23);
    printf("  Group4 :"); decode_group(pkt + 34);
    printf("\n");
}





int main(void) {
    /* Captured frame from their system (Capture 2, Frame 0) */
    static const uint8_t captured[PACKET_SIZE] = {
        0x80, 0xc0,
        0x08, 0x02, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01,
        0xc1,
        0xf9, 0x01, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01,
        0xc2,
        0xe9, 0x01, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01,
        0xc3,
        0xf7, 0x01, 0x08, 0x02, 0xf9, 0x01, 0xe9, 0x01, 0xf7, 0x01
    };



    /* Our packet for a test weight — use average of captured P values:
       P1=520(0x0208), P2=505(0x01f9), P3=489(0x01e9), P4=503(0x01f7)
       average counts ~ 504, so weight = 504/10 = 50.4 kg */
    double test_weight = 50.4;
    uint8_t our_packet[PACKET_SIZE];
    build_packet(test_weight, our_packet);

    printf("========================================\n");
    printf("  Packet Format Comparison\n");
    printf("========================================\n\n");

    print_hex("CAPTURED (their packet):", captured, PACKET_SIZE);
    print_hex("OURS (bridge output)   :", our_packet, PACKET_SIZE);

    /* Byte-by-byte structure comparison */
    printf("Structure check:\n");
    printf("  Header  [0]   : captured=%02x    ours=%02x    %s\n",
           captured[0], our_packet[0],
           captured[0]==our_packet[0] ? "OK" : "MISMATCH");
    printf("  Marker1 [1]   : captured=%02x    ours=%02x    %s\n",
           captured[1], our_packet[1],
           captured[1]==our_packet[1] ? "OK" : "MISMATCH");
    printf("  Marker2 [12]  : captured=%02x    ours=%02x    %s\n",
           captured[12], our_packet[12],
           captured[12]==our_packet[12] ? "OK" : "MISMATCH");
    printf("  Marker3 [23]  : captured=%02x    ours=%02x    %s\n",
           captured[23], our_packet[23],
           captured[23]==our_packet[23] ? "OK" : "MISMATCH");
    printf("  Marker4 [34]  : captured=%02x    ours=%02x    %s\n",
           captured[34], our_packet[34],
           captured[34]==our_packet[34] ? "OK" : "MISMATCH");
    printf("  Total size    : captured=%d       ours=%d       %s\n\n",
           PACKET_SIZE, PACKET_SIZE, "OK");

    decode_packet("CAPTURED", captured);
    decode_packet("OURS    ", our_packet);

    return 0;
}




