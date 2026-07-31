/*
 * reader.c — Weighing Terminal Live Reader
 *
 * Connects to the weighing terminal over TCP, displays weight live,
 * supports 3 modes.
 *
 * Compile:  gcc -o reader reader.c -lpthread
 * Run:      ./reader
 *
 * Keys: 1 = kg  |  2 = 1:1 ton  |  3 = 1:2 ton  |  Ctrl+C = stop
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <pthread.h>
#include <termios.h>
#include <arpa/inet.h>
#include <sys/socket.h>

/* ── Config ────────────────────────────────────────────────────────────────── */
#define TCP_HOST    "192.168.0.1"
#define TCP_PORT    1702
#define LOCAL_IP    "192.168.0.100"
#define HISTORY     100
#define MODE_FILE   "/tmp/weight_mode"
/* ─────────────────────────────────────────────────────────────────────────── */

typedef struct {
    double  vals[HISTORY];
    int     head;
    int     count;
} RingBuf;

static volatile int      current_mode = 1;
static volatile int      running      = 1;
static pthread_mutex_t   mode_lock    = PTHREAD_MUTEX_INITIALIZER;
static struct termios    orig_termios;

/* ── Ring buffer ─────────────────────────────────────────────────────────────*/
static void rb_push(RingBuf *rb, double v) {
    rb->vals[rb->head] = v;
    rb->head = (rb->head + 1) % HISTORY;
    if (rb->count < HISTORY) rb->count++;
}

static double rb_min(RingBuf *rb) {
    double m = rb->vals[0];
    for (int i = 1; i < rb->count; i++)
        if (rb->vals[i] < m) m = rb->vals[i];
    return m;
}

static double rb_max(RingBuf *rb) {
    double m = rb->vals[0];
    for (int i = 1; i < rb->count; i++)
        if (rb->vals[i] > m) m = rb->vals[i];
    return m;
}

static double rb_mean(RingBuf *rb) {
    double s = 0;
    for (int i = 0; i < rb->count; i++) s += rb->vals[i];
    return rb->count ? s / rb->count : 0.0;
}

/* ── Mode helpers ────────────────────────────────────────────────────────────*/
static const char *mode_unit(int m) {
    return (m == 1) ? "kg" : "ton";
}

static const char *mode_desc(int m) {
    switch (m) {
        case 1: return "Real-time kg";
        case 2: return "1 kg = 1 ton";
        case 3: return "1 kg = 2 ton";
    }
    return "";
}

static double mode_convert(int m, double kg) {
    switch (m) {
        case 1: return kg;
        case 2: return kg;
        case 3: return kg * 2.0;
    }
    return kg;
}

/* ── Display ─────────────────────────────────────────────────────────────────*/



static int display_init = 0;
#define NLINES 15

static void print_live(long packet_no, double display_val, int mode, RingBuf *rb)
{
    const char *unit = mode_unit(mode);
    double w_min  = rb_min(rb);
    double w_max  = rb_max(rb);
    double w_mean = rb_mean(rb);

    char lines[NLINES][80];
    snprintf(lines[0],  80, "==================================================");
    snprintf(lines[1],  80, "  Weighing Terminal  |  Packet #%-10ld", packet_no);
    snprintf(lines[2],  80, "==================================================");
    lines[3][0] = '\0';
    snprintf(lines[4],  80, "  MODE   : [%d] %-30s", mode, mode_desc(mode));
    lines[5][0] = '\0';
    snprintf(lines[6],  80, "  WEIGHT :  %8.2f %-5s", display_val, unit);
    lines[7][0] = '\0';
    snprintf(lines[8],  80, "  Last %d readings (%s):", rb->count, unit);
    snprintf(lines[9],  80, "    min  = %.2f %s", w_min,  unit);
    snprintf(lines[10], 80, "    max  = %.2f %s", w_max,  unit);
    snprintf(lines[11], 80, "    mean = %.2f %s", w_mean, unit);
    snprintf(lines[12], 80, "==================================================");
    lines[13][0] = '\0';
    snprintf(lines[14], 80, "  Keys: 1=kg  2=1:1 ton  3=1:2 ton  |  Ctrl+C stop");

    if (!display_init) {
        for (int i = 0; i < NLINES; i++)
            printf("%s\n", lines[i]);
        /* save cursor position after initial print */
        printf("\x1b[%dA", NLINES);   /* move up */
        display_init = 1;
    }

    /* restore to top of block, erase and rewrite each line */
    for (int i = 0; i < NLINES; i++)
        printf("\x1b[2K%s\n", lines[i]);

    fflush(stdout);
}

/* ── Raw terminal (non-blocking keypress) ────────────────────────────────────*/
static void term_raw(void) {
    struct termios t;
    tcgetattr(STDIN_FILENO, &orig_termios);
    t = orig_termios;
    t.c_lflag &= ~(ICANON | ECHO);
    t.c_cc[VMIN]  = 0;
    t.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSANOW, &t);
}

static void term_restore(void) {
    tcsetattr(STDIN_FILENO, TCSANOW, &orig_termios);
}

/* ── Keypress thread ─────────────────────────────────────────────────────────*/
static void *keypress_thread(void *arg) {
    (void)arg;
    char ch;
    while (running) {
        ssize_t n = read(STDIN_FILENO, &ch, 1);
        if (n <= 0) { usleep(10000); continue; }
        if (ch == '1' || ch == '2' || ch == '3') {
            pthread_mutex_lock(&mode_lock);
            current_mode = ch - '0';
            pthread_mutex_unlock(&mode_lock);
            /* Write mode to file so bridge can read it */
            FILE *f = fopen(MODE_FILE, "w");
            if (f) { fprintf(f, "%d\n", ch - '0'); fclose(f); }
        } else if (ch == 3) {  /* Ctrl+C */
            running = 0;
            break;
        }
    }
    return NULL;
}

/* ── Signal handler ──────────────────────────────────────────────────────────*/
static void handle_sigint(int s) {
    (void)s;
    running = 0;
}


/* ── TCP connect ─────────────────────────────────────────────────────────────*/
static int tcp_connect(void) {
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) { perror("socket"); return -1; }

    struct sockaddr_in local = {0};
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = inet_addr(LOCAL_IP);
    local.sin_port = 0;
    if (bind(sock, (struct sockaddr *)&local, sizeof(local)) < 0) {
        perror("bind"); close(sock); return -1;
    }

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



/* ── Main ────────────────────────────────────────────────────────────────────*/
int main(void) {
    signal(SIGINT, handle_sigint);
    term_raw();
    atexit(term_restore);

    /* Write default mode to file */
    { FILE *f = fopen(MODE_FILE, "w"); if (f) { fprintf(f, "1\n"); fclose(f); } }

    printf("Connecting to %s:%d ...\n", TCP_HOST, TCP_PORT);
    int sock = tcp_connect();
    if (sock < 0) return 1;
    printf("Connected. Reading data (Ctrl+C to stop)...\n\n");

    /* Start keypress thread */
    pthread_t kt;
    pthread_create(&kt, NULL, keypress_thread, NULL);

    RingBuf rb = {0};
    long packet_no = 0;
    char buf[4096];
    int  buf_len = 0

    while (running) {
        ssize_t n = recv(sock, buf + buf_len, sizeof(buf) - buf_len - 1, 0);
        if (n <= 0) {
            if (!running) break;
            printf("\nConnection closed.\n");
            break;
        }
        buf_len += n;
        buf[buf_len] = '\0';

        /* Process complete lines */
        char *line_start = buf;
        char *newline;
        while ((newline = strchr(line_start, '\n')) != NULL) {
            *newline = '\0';

            /* Strip \r and spaces */
            char *p = line_start;
            while (*p == ' ' || *p == '\r' || *p == '\t') p++;
            char *end = newline - 1;
            while (end > p && (*end == ' ' || *end == '\r')) end--;
            *(end + 1) = '\0';

            if (*p != '\0') {
                double weight_kg = 0.0;
                if (sscanf(p, "%lf", &weight_kg) == 1) 
                {
                    packet_no++;

                    pthread_mutex_lock(&mode_lock);
                    int mode = current_mode;
                    pthread_mutex_unlock(&mode_lock);

                    double display_val = mode_convert(mode, weight_kg);
                    rb_push(&rb, display_val);
                    print_live(packet_no, display_val, mode, &rb);
                }
            }

            line_start = newline + 1;
        }

        /* Move remaining partial line to front of buffer */
        buf_len = (int)(buf + buf_len - line_start);
        memmove(buf, line_start, buf_len);
    }

    running = 0;
    pthread_join(kt, NULL);
    close(sock);
    printf("\n\nStopped.\n");
    return 0;
}


    




