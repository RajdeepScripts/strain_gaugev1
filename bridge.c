/*
 * bridge.c — Weight Bridge
 *
 * Reads plain-text weight from the weighing terminal over TCP,
 * converts it to the binary packet format, and writes it to a
 * virtual serial port for the company software.
 *
 * Packet format (45 bytes):
 *   0x80 0xC0 | Group1(10) | 0xC1 | Group2(10) | 0xC2 | Group3(10) | 0xC3 | Group4(10)
 *   Group = S1 P1 P2 P3 P4  (5 x int16 little-endian)
 *
 * Compile:  gcc -o bridge bridge.c
 * Run:
 *   Step 1: socat -d -d pty,raw,echo=0,link=/tmp/ttyV0 pty,raw,echo=0,link=/tmp/ttyV1
 *   Step 2: TVEMA at /tmp/ttyV1
 *   Step 3: ./bridge
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <time.h>
#include <signal.h>
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>

/* ── Config ────────────────────────────────────────────────────────────────── */
#define TCP_HOST     "192.168.0.1"
#define TCP_PORT     1702
#define LOCAL_IP     "192.168.0.100"
#define SERIAL_PORT  "/dev/pts/8"
#define BAUD_RATE    B115200
/* SCALE: counts per kg. int16 max=32767, so:
   SCALE=10  → max 3276.7 kg, resolution 0.1 kg
   SCALE=100 → max  327.6 kg, resolution 0.01 kg  */
#define SCALE        10
/* ─────────────────────────────────────────────────────────────────────────── */

#define PACKET_SIZE  45

static volatile int running = 1;

static void handle_sigint(int s) { (void)s; running = 0; }

/* ── Clamp to int16 range ────────────────────────────────────────────────────*/
static int16_t kg_to_counts(double kg) {
    long counts = lround(kg * SCALE);
    if (counts >  32767) counts =  32767;
    if (counts < -32768) counts = -32768;
    return (int16_t)counts;
}

/* ── Write int16 little-endian into buf ──────────────────────────────────────*/
static void write_le16(uint8_t *buf, int16_t val) {
    buf[0] = (uint8_t)(val & 0xFF);
    buf[1] = (uint8_t)((val >> 8) & 0xFF);
}

/* ── Build 45-byte packet ────────────────────────────────────────────────────*/
static void build_packet(double weight_kg, uint8_t *pkt) {
    int16_t counts = kg_to_counts(weight_kg);

    /* Group: S1=0, P1=P2=P3=P4=counts (5 x int16 LE = 10 bytes) */
    uint8_t group[10];
    write_le16(group + 0, 0);       /* S1 */
    write_le16(group + 2, counts);  /* P1 */
    write_le16(group + 4, counts);  /* P2 */
    write_le16(group + 6, counts);  /* P3 */
    write_le16(group + 8, counts);  /* P4 */

    int i = 0;
    pkt[i++] = 0x80;
    pkt[i++] = 0xC0;
    memcpy(pkt + i, group, 10); i += 10;
    pkt[i++] = 0xC1;
    memcpy(pkt + i, group, 10); i += 10;
    pkt[i++] = 0xC2;
    memcpy(pkt + i, group, 10); i += 10;
    pkt[i++] = 0xC3;
    memcpy(pkt + i, group, 10);
}

/* ── Open serial port ────────────────────────────────────────────────────────*/
static int open_serial(const char *port) {
    int fd = open(port, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) { perror("open serial"); return -1; }

    struct termios t;
    tcgetattr(fd, &t);
    cfsetispeed(&t, BAUD_RATE);
    cfsetospeed(&t, BAUD_RATE);
    t.c_cflag = (t.c_cflag & ~CSIZE) | CS8;  /* 8 data bits */
    t.c_cflag &= ~PARENB;                     /* no parity   */
    t.c_cflag &= ~CSTOPB;                     /* 1 stop bit  */
    t.c_cflag &= ~CRTSCTS;                    /* no flow ctrl*/
    t.c_cflag |= CREAD | CLOCAL;
    t.c_lflag = 0;
    t.c_iflag = 0;
    t.c_oflag = 0;
    tcsetattr(fd, TCSANOW, &t);
    return fd;
}

/* ── TCP connect ─────────────────────────────────────────────────────────────*/
static int tcp_connect(void) {
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) { perror("socket"); return -1; }

    struct sockaddr_in local = {0};
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = inet_addr(LOCAL_IP);
    bind(sock, (struct sockaddr *)&local, sizeof(local));

    struct timeval tv = {10, 0};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in server = {0};
    server.sin_family = AF_INET;
    server.sin_addr.s_addr = inet_addr(TCP_HOST);
    server.sin_port = htons(TCP_PORT);

    if (connect(sock, (struct sockaddr *)&server, sizeof(server)) < 0) {
        perror("connect"); close(sock); return -1;
    }
    return sock;
}

/* ── Timestamp ───────────────────────────────────────────────────────────────*/
static void timestamp(char *buf, size_t n) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    struct tm *tm = localtime(&tv.tv_sec);
    snprintf(buf, n, "%02d:%02d:%02d.%03d",
             tm->tm_hour, tm->tm_min, tm->tm_sec, (int)(tv.tv_usec / 1000));
}

/* ── Main ────────────────────────────────────────────────────────────────────*/
int main(void) {
    signal(SIGINT, handle_sigint);

    printf("=======================================================\n");
    printf("  Weight Bridge\n");
    printf("=======================================================\n");
    printf("  TCP source  : %s:%d\n", TCP_HOST, TCP_PORT);
    printf("  Serial sink : %s @ %d baud\n", SERIAL_PORT, 115200);
    printf("  Scale       : %d counts/kg\n", SCALE);
    printf("  Resolution  : %.2f kg\n", 1.0 / SCALE); 
    printf("  Max weight  : %.1f kg\n", 32767.0 / SCALE);
    printf("=======================================================\n\n");

    printf("Opening serial port %s ...\n", SERIAL_PORT);
    int ser = open_serial(SERIAL_PORT);
    if (ser < 0) {
        printf("  ERROR: Cannot open serial port.\n");
        printf("  Make sure socat is running:\n");
        printf("  socat -d -d pty,raw,echo=0,link=/tmp/ttyV0 pty,raw,echo=0,link=/tmp/ttyV1\n");
        return 1;
    }
    printf("  Serial OK\n");

    printf("Connecting to terminal %s:%d ...\n", TCP_HOST, TCP_PORT);
    int sock = tcp_connect();
    if (sock < 0) { close(ser); return 1; }
    printf("  TCP OK\n\n");

    printf("  Bridging data... (Ctrl+C to stop)\n\n");
    printf("  %-14s  %12s  %8s  %8s\n", "Time", "Weight (kg)", "Counts", "Packets");
    printf("  %s\n", "----------------------------------------------------");

    char     tcp_buf[4096];
    int      buf_len = 0;
    long     packets = 0;
    uint8_t  pkt[PACKET_SIZE];

    while (running) {
        ssize_t n = recv(sock, tcp_buf + buf_len, sizeof(tcp_buf) - buf_len - 1, 0);
        if (n <= 0) {
            if (!running) break;
            printf("\nTCP connection closed.\n");
            break;
        }
        buf_len += n;
        tcp_buf[buf_len] = '\0';

        char *line_start = tcp_buf;
        char *newline;
        while ((newline = strchr(line_start, '\n')) != NULL) {
            *newline = '\0';

            char *p = line_start;
            while (*p == ' ' || *p == '\r' || *p == '\t') p++;

            double weight_kg = 0.0;
            if (*p != '\0' && sscanf(p, "%lf", &weight_kg) == 1) {
                build_packet(weight_kg, pkt);
                if (write(ser, pkt, PACKET_SIZE) < 0) perror("write serial");
                packets++;

                int16_t counts = kg_to_counts(weight_kg);
                char ts[16];
                timestamp(ts, sizeof(ts));
                printf("  %-14s  %12.3f  %8d  %8ld\r",
                       ts, weight_kg, counts, packets);
                fflush(stdout);
            }

            line_start = newline + 1;
        }

        buf_len = (int)(tcp_buf + buf_len - line_start);
        memmove(tcp_buf, line_start, buf_len);
    }

    close(sock);
    close(ser);
    printf("\n\nStopped. Total packets sent: %ld\n", packets);
    return 0;
}
