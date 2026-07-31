/*
 * basic_interface.c — 4x4 Basic Interface
 *
 * Exact replica of the Russian weighing software GUI.
 *
 * Compile:
 *   gcc -o basic_interface basic_interface.c \
 *       $(pkg-config --cflags --libs gtk+-3.0) -lpthread -lm
 * Run:
 *   ./basic_interface
 */

#include <gtk/gtk.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <pthread.h>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

/* ── Config ──────────────────────────────────────────────────────────────── */
#define SERIAL_PORT  "/dev/ttyUSB0"
#define BAUD_RATE    B115200
#define PKT_SIZE     45
#define AVG_MAX      1000

/* ── Calibration ─────────────────────────────────────────────────────────── */
static const double ZERO[4][4] = {
    {-755.0, -2594.7,  1361.0, -3485.7},
    {-752.3, -2595.0,  1359.3, -3483.7},
    {-751.0, -2595.7,  1359.3, -3483.0},
    {-749.0, -2597.3,  1357.0, -3483.7},
};
static const double SENS[4][4] = {
    {1.6186, 1.2491, 1.0482, 0.9479},
    {1.6026, 1.2511, 1.0585, 0.9360},
    {1.5947, 1.2555, 1.0586, 0.9314},
    {1.5825, 1.2652, 1.0723, 0.9357},
};
#define KG_TO_KN 0.00981



/* ── Shared state ─────────────────────────────────────────────────────────── */
typedef struct {
    double q[4];         /* raw avg count per channel  */
    double kn[4];        /* kN per channel              */
    double temp[4];      /* cfg field                   */
    double result_kg;
    double result_kn;
    long   last_bytes;
    int    valid;        /* 0=no data, 1=ok, -1=error   */
} SharedData;

static SharedData      g_data;
static pthread_mutex_t g_lock   = PTHREAD_MUTEX_INITIALIZER;
static volatile int    g_running = 1;

static double *g_avg_buf   = NULL;
static int     g_avg_head  = 0;
static int     g_avg_count = 0;
static int     g_avg_win   = 100;

/* ── Widgets ──────────────────────────────────────────────────────────────── */
typedef struct {
    GtkWidget *main_display;   /* tall value box         */
    GtkWidget *kn_box;         /* small kN box           */
    GtkWidget *q_box;          /* small q box            */
    GtkWidget *temp_entry;
} ChanW;

static ChanW       g_ch[4];
static GtkWidget  *g_result_tall;   /* large result display  */
static GtkWidget  *g_s_box;         /* S= small box          */
static GtkWidget  *g_status_label;
static GtkWidget  *g_avg_spin;
static GtkWidget  *g_radio_kg;
static GtkWidget  *g_radio_kn;

/* ── Packet decode ────────────────────────────────────────────────────────── */
static int is_valid_pkt(const uint8_t *d) {
    return d[0]==0x80 && d[1]==0xC0 && d[12]==0xC1 && d[23]==0xC2 && d[34]==0xC3;
}

static void decode_packet(const uint8_t *pkt, long bytes_total) {
    double sensor_kg[4], q_val[4], temp_val[4];

    for (int s = 0; s < 4; s++) {
        int base = 1 + s * 11;
        int16_t cfg, p1, p2, p3, p4;
        memcpy(&cfg, pkt+base+1, 2);
        memcpy(&p1,  pkt+base+3, 2);
        memcpy(&p2,  pkt+base+5, 2);
        memcpy(&p3,  pkt+base+7, 2);
        memcpy(&p4,  pkt+base+9, 2);

        int16_t arms[4] = {p1, p2, p3, p4};
        double arm_kg[4];
        for (int a = 0; a < 4; a++)
            arm_kg[a] = ((double)arms[a] - ZERO[s][a]) / SENS[s][a];

        sensor_kg[s] = (arm_kg[0]+arm_kg[1]+arm_kg[2]+arm_kg[3]) / 4.0;
        q_val[s]     = (p1+p2+p3+p4) / 4.0;
        temp_val[s]  = cfg;
    }

    double total_kg = sensor_kg[0]+sensor_kg[1]+sensor_kg[2]+sensor_kg[3];

    if (g_avg_buf) {
        g_avg_buf[g_avg_head] = total_kg;
        g_avg_head = (g_avg_head + 1) % g_avg_win;
        if (g_avg_count < g_avg_win) g_avg_count++;
        double sum = 0;
        for (int i = 0; i < g_avg_count; i++) sum += g_avg_buf[i];
        double avg_kg = sum / g_avg_count;

        pthread_mutex_lock(&g_lock);
        for (int s = 0; s < 4; s++) {
            g_data.q[s]    = q_val[s];
            g_data.kn[s]   = sensor_kg[s] * KG_TO_KN;
            g_data.temp[s] = temp_val[s];
        }
        g_data.result_kg  = avg_kg;
        g_data.result_kn  = avg_kg * KG_TO_KN;
        g_data.last_bytes = bytes_total;
        g_data.valid      = 1;
        pthread_mutex_unlock(&g_lock);
    }
}

/* ── Serial thread ────────────────────────────────────────────────────────── */
static void *reader_thread(void *arg) {
    (void)arg;

    int fd = open(SERIAL_PORT, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        pthread_mutex_lock(&g_lock);
        g_data.valid = -1;
        pthread_mutex_unlock(&g_lock);
        return NULL;
    }

    struct termios t;
    tcgetattr(fd, &t);
    cfsetispeed(&t, BAUD_RATE);
    cfsetospeed(&t, BAUD_RATE);
    t.c_cflag = (t.c_cflag & ~CSIZE) | CS8;
    t.c_cflag &= ~(PARENB | CSTOPB | CRTSCTS);
    t.c_cflag |= CREAD | CLOCAL;
    t.c_lflag = 0; t.c_iflag = 0; t.c_oflag = 0;
    tcsetattr(fd, TCSANOW, &t);

    uint8_t buf[PKT_SIZE * 8];
    int  buf_len    = 0;
    long bytes_total = 0;
 
    while (g_running) {
        int n = read(fd, buf + buf_len, sizeof(buf) - buf_len);
        if (n <= 0) { usleep(5000); continue; }
        buf_len     += n;
        bytes_total += n;

        while (buf_len >= PKT_SIZE) {
            int idx = -1;
            for (int i = 0; i < buf_len; i++)
                if (buf[i] == 0x80) { idx = i; break; }
            if (idx < 0) { buf_len = 0; break; }
            if (idx > 0) { memmove(buf, buf+idx, buf_len-idx); buf_len -= idx; }
            if (buf_len < PKT_SIZE) break;

            if (is_valid_pkt(buf)) {
                decode_packet(buf, bytes_total);
                memmove(buf, buf+PKT_SIZE, buf_len-PKT_SIZE);
                buf_len -= PKT_SIZE;
            } else {
                memmove(buf, buf+1, buf_len-1);
                buf_len--;
            }
        }
    }
    close(fd);
    return NULL;
}

/* ── Helper: make a display entry (white bg, bold font, read-only) ────────── */
static GtkWidget *make_display(int width_chars, int height_px) {
    GtkWidget *e = gtk_entry_new();
    gtk_entry_set_width_chars(GTK_ENTRY(e), width_chars);
    gtk_editable_set_editable(GTK_EDITABLE(e), FALSE);
    gtk_widget_set_size_request(e, -1, height_px);
    gtk_widget_set_name(e, "display_box");
    gtk_entry_set_alignment(GTK_ENTRY(e), 0.5);
    return e;
}

/* ── UI update ────────────────────────────────────────────────────────────── */
static gboolean update_ui(gpointer ud) {
    (void)ud;
    SharedData d;
    pthread_mutex_lock(&g_lock);
    d = g_data;
    pthread_mutex_unlock(&g_lock);

    if (d.valid == -1) {
        gtk_label_set_text(GTK_LABEL(g_status_label),
            "ERROR: Cannot open " SERIAL_PORT);
        return G_SOURCE_CONTINUE;
    }
    if (!d.valid) return G_SOURCE_CONTINUE;

    gboolean use_kn = gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(g_radio_kn));
    char buf[64];

    for (int ch = 0; ch < 4; ch++) {
        /* main tall display: show q value */
        snprintf(buf, sizeof(buf), "%.0f", d.q[ch]);
        gtk_entry_set_text(GTK_ENTRY(g_ch[ch].main_display), buf);

        snprintf(buf, sizeof(buf), "%.1f", d.kn[ch]);
        gtk_entry_set_text(GTK_ENTRY(g_ch[ch].kn_box), buf);

        snprintf(buf, sizeof(buf), "%.0f", d.q[ch]);
        gtk_entry_set_text(GTK_ENTRY(g_ch[ch].q_box), buf);

        snprintf(buf, sizeof(buf), "%.0f", d.temp[ch]);
        gtk_entry_set_text(GTK_ENTRY(g_ch[ch].temp_entry), buf);
    }

    double result = use_kn ? d.result_kn : d.result_kg;
    snprintf(buf, sizeof(buf), use_kn ? "%.2f" : "%.0f", result);
    gtk_entry_set_text(GTK_ENTRY(g_result_tall), buf);
    gtk_entry_set_text(GTK_ENTRY(g_s_box),       buf);

    snprintf(buf, sizeof(buf),
             "Last packages: —   Bytes on port: %ld", d.last_bytes);
    gtk_label_set_text(GTK_LABEL(g_status_label), buf);

    return G_SOURCE_CONTINUE;
}

/* ── Callbacks ────────────────────────────────────────────────────────────── */
static void on_set_zero(GtkButton *b, gpointer d) {
    (void)b; (void)d;
    pthread_mutex_lock(&g_lock);
    g_avg_head = g_avg_count = 0;
    if (g_avg_buf) memset(g_avg_buf, 0, sizeof(double)*g_avg_win);
    pthread_mutex_unlock(&g_lock);
}

static void on_avg_changed(GtkSpinButton *spin, gpointer d) {
    (void)d;
    int newval = (int)gtk_spin_button_get_value(spin);
    if (newval < 1) newval = 1;
    if (newval > AVG_MAX) newval = AVG_MAX;
    pthread_mutex_lock(&g_lock);
    free(g_avg_buf);
    g_avg_buf   = calloc(newval, sizeof(double));
    g_avg_win   = newval;
    g_avg_head  = 0;
    g_avg_count = 0;
    pthread_mutex_unlock(&g_lock);
}

static void on_exit_clicked(GtkButton *b, gpointer d) {
    (void)b; (void)d;
    g_running = 0;
    gtk_main_quit();
}

/* ── Main ─────────────────────────────────────────────────────────────────── */


int main(int argc, char *argv[]) {
    gtk_init(&argc, &argv);

    g_avg_buf = calloc(g_avg_win, sizeof(double));

    /* CSS */
    GtkCssProvider *css = gtk_css_provider_new();
    gtk_css_provider_load_from_data(css,
        "window, box, frame { background-color: #f0f0f0; }"
        "#display_box {"
        "  background-color: white;"
        "  font-family: monospace;"
        "  font-size: 18px;"
        "  font-weight: bold;"
        "  color: black;"
        "}"
        "#result_tall {"
        "  background-color: white;"
        "  font-family: monospace;"
        "  font-size: 28px;"
        "  font-weight: bold;"
        "  color: black;"
        "}"
        , -1, NULL);
    gtk_style_context_add_provider_for_screen(gdk_screen_get_default(),
        GTK_STYLE_PROVIDER(css), GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);

    /* Window */
    GtkWidget *win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    gtk_window_set_title(GTK_WINDOW(win), "4x4 Basic Interface");
    gtk_window_set_default_size(GTK_WINDOW(win), 1000, 580);
    gtk_container_set_border_width(GTK_CONTAINER(win), 6);
    g_signal_connect(win, "destroy", G_CALLBACK(gtk_main_quit), NULL);

    GtkWidget *root = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    gtk_container_add(GTK_CONTAINER(win), root);

    /* ── Controls bar ── */
    GtkWidget *ctrl = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_box_pack_start(GTK_BOX(root), ctrl, FALSE, FALSE, 2);

    GtkWidget *frame_ctrl = gtk_frame_new("Controls");
    gtk_box_pack_start(GTK_BOX(ctrl), frame_ctrl, FALSE, FALSE, 0);
    GtkWidget *ctrl_inner = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_container_set_border_width(GTK_CONTAINER(ctrl_inner), 4);
    gtk_container_add(GTK_CONTAINER(frame_ctrl), ctrl_inner);

    /* ID label */
    gtk_box_pack_start(GTK_BOX(ctrl_inner), gtk_label_new("ID"), FALSE, FALSE, 0);

    GtkWidget *btn_close = gtk_button_new_with_label("Close");
    g_signal_connect(btn_close, "clicked", G_CALLBACK(on_exit_clicked), NULL);
    gtk_box_pack_start(GTK_BOX(ctrl_inner), btn_close, FALSE, FALSE, 0);

    GtkWidget *combo = gtk_combo_box_text_new();
    gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(combo), "COM7");
    gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(combo), SERIAL_PORT);
    gtk_combo_box_set_active(GTK_COMBO_BOX(combo), 1);
    gtk_box_pack_start(GTK_BOX(ctrl_inner), combo, FALSE, FALSE, 0);

    GtkWidget *btn_update = gtk_button_new_with_label("Update");
    gtk_box_pack_start(GTK_BOX(ctrl_inner), btn_update, FALSE, FALSE, 0);

    /* k factor — right side of controls */
    GtkWidget *k_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_box_pack_end(GTK_BOX(ctrl), k_box, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(k_box), gtk_label_new("k ="), FALSE, FALSE, 0);
    GtkWidget *k_entry = gtk_entry_new();
    gtk_entry_set_text(GTK_ENTRY(k_entry), "0.0067563");
    gtk_entry_set_width_chars(GTK_ENTRY(k_entry), 12);
    gtk_box_pack_start(GTK_BOX(k_box), k_entry, FALSE, FALSE, 0);

    GtkWidget *btn_cancel = gtk_button_new_with_label("Cancel Zero");
    gtk_box_pack_start(GTK_BOX(k_box), btn_cancel, FALSE, FALSE, 0);

    /* ── Channel data + Result row ── */
    GtkWidget *main_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_box_pack_start(GTK_BOX(root), main_row, TRUE, TRUE, 0);

    /* Channel data frame */
    GtkWidget *ch_frame = gtk_frame_new("Chanel data");
    gtk_box_pack_start(GTK_BOX(main_row), ch_frame, TRUE, TRUE, 0);
    GtkWidget *ch_hbox = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_container_set_border_width(GTK_CONTAINER(ch_hbox), 6);
    gtk_container_add(GTK_CONTAINER(ch_frame), ch_hbox);

    for (int ch = 0; ch < 4; ch++) {
        GtkWidget *col = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
        gtk_box_pack_start(GTK_BOX(ch_hbox), col, TRUE, TRUE, 2);

        /* Channel title + temp on same line */
        GtkWidget *title_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
        gtk_box_pack_start(GTK_BOX(col), title_row, FALSE, FALSE, 0);
        char lbl[32];
        snprintf(lbl, sizeof(lbl), "Chanel %d", ch+1);
        gtk_box_pack_start(GTK_BOX(title_row), gtk_label_new(lbl), FALSE, FALSE, 0);

        /* temp row */
        GtkWidget *temp_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 2);
        gtk_box_pack_start(GTK_BOX(col), temp_row, FALSE, FALSE, 0);
        gtk_box_pack_start(GTK_BOX(temp_row), gtk_label_new("temp ="), FALSE, FALSE, 0);
        g_ch[ch].temp_entry = gtk_entry_new();
        gtk_entry_set_width_chars(GTK_ENTRY(g_ch[ch].temp_entry), 5);
        gtk_editable_set_editable(GTK_EDITABLE(g_ch[ch].temp_entry), FALSE);
        gtk_box_pack_start(GTK_BOX(temp_row), g_ch[ch].temp_entry, FALSE, FALSE, 0);

        /* Main tall display */
        g_ch[ch].main_display = make_display(8, 120);
        gtk_widget_set_name(g_ch[ch].main_display, "display_box");
        gtk_box_pack_start(GTK_BOX(col), g_ch[ch].main_display, TRUE, TRUE, 0);

        /* Two small boxes: kN and q side by side */
        GtkWidget *small_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
        gtk_box_pack_start(GTK_BOX(col), small_row, FALSE, FALSE, 0);

        GtkWidget *kn_col = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
        gtk_box_pack_start(GTK_BOX(small_row), kn_col, TRUE, TRUE, 0);
        g_ch[ch].kn_box = make_display(6, 40);
        gtk_box_pack_start(GTK_BOX(kn_col), g_ch[ch].kn_box, FALSE, FALSE, 0);
        gtk_box_pack_start(GTK_BOX(kn_col), gtk_label_new("Tones"), FALSE, FALSE, 0);

        GtkWidget *q_col = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
        gtk_box_pack_start(GTK_BOX(small_row), q_col, TRUE, TRUE, 0);
        g_ch[ch].q_box = make_display(6, 40);
        gtk_box_pack_start(GTK_BOX(q_col), g_ch[ch].q_box, FALSE, FALSE, 0);
        gtk_box_pack_start(GTK_BOX(q_col), gtk_label_new("q"), FALSE, FALSE, 0);

        /* Separator between channels */
        if (ch < 3)
            gtk_box_pack_start(GTK_BOX(ch_hbox),
                gtk_separator_new(GTK_ORIENTATION_VERTICAL), FALSE, FALSE, 0);
    }

    /* Result frame — right side */
    GtkWidget *res_frame = gtk_frame_new("Result");
    gtk_box_pack_start(GTK_BOX(main_row), res_frame, FALSE, FALSE, 0);
    GtkWidget *res_vbox = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    gtk_container_set_border_width(GTK_CONTAINER(res_vbox), 8);
    gtk_container_add(GTK_CONTAINER(res_frame), res_vbox);

    /* Large tall result display */
    g_result_tall = gtk_entry_new();
    gtk_entry_set_width_chars(GTK_ENTRY(g_result_tall), 10);
    gtk_widget_set_size_request(g_result_tall, 160, 180);
    gtk_editable_set_editable(GTK_EDITABLE(g_result_tall), FALSE);
    gtk_widget_set_name(g_result_tall, "result_tall");
    gtk_entry_set_alignment(GTK_ENTRY(g_result_tall), 0.5);
    gtk_box_pack_start(GTK_BOX(res_vbox), g_result_tall, TRUE, TRUE, 0);

    /* S= small box */
    GtkWidget *s_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_box_pack_start(GTK_BOX(res_vbox), s_row, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(s_row), gtk_label_new("S ="), FALSE, FALSE, 0);
    g_s_box = make_display(8, 40);
    gtk_box_pack_start(GTK_BOX(s_row), g_s_box, FALSE, FALSE, 0);

    /* kg / kN radio buttons */
    g_radio_kn = gtk_radio_button_new_with_label(NULL, "kN");
    g_radio_kg = gtk_radio_button_new_with_label_from_widget(
                     GTK_RADIO_BUTTON(g_radio_kn), "kg");
    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(g_radio_kg), TRUE);
    gtk_box_pack_start(GTK_BOX(res_vbox), g_radio_kn, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(res_vbox), g_radio_kg, FALSE, FALSE, 0);

    /* Set Zero button in result panel */
    GtkWidget *btn_zero = gtk_button_new_with_label("Set Zero");
    g_signal_connect(btn_zero, "clicked", G_CALLBACK(on_set_zero), NULL);
    gtk_box_pack_start(GTK_BOX(res_vbox), btn_zero, FALSE, FALSE, 0);

    /* ── Bottom bar: avg + status + exit ── */
    GtkWidget *bot = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_box_pack_end(GTK_BOX(root), bot, FALSE, FALSE, 2);

    gtk_box_pack_start(GTK_BOX(bot), gtk_label_new("Average by"), FALSE, FALSE, 0);
    GtkAdjustment *adj = gtk_adjustment_new(100, 1, AVG_MAX, 1, 10, 0);
    g_avg_spin = gtk_spin_button_new(adj, 1, 0);
    g_signal_connect(g_avg_spin, "value-changed", G_CALLBACK(on_avg_changed), NULL);
    gtk_box_pack_start(GTK_BOX(bot), g_avg_spin, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(bot), gtk_label_new("measurements"), FALSE, FALSE, 0);

    gtk_box_pack_start(GTK_BOX(bot),
        gtk_separator_new(GTK_ORIENTATION_VERTICAL), FALSE, FALSE, 4);

    g_status_label = gtk_label_new("Last packages: —   Bytes on port: 0");
    gtk_label_set_xalign(GTK_LABEL(g_status_label), 0.0);
    gtk_box_pack_start(GTK_BOX(bot), g_status_label, TRUE, TRUE, 0);

    GtkWidget *btn_exit = gtk_button_new_with_label("Exit program");
    g_signal_connect(btn_exit, "clicked", G_CALLBACK(on_exit_clicked), NULL);
    gtk_box_pack_end(GTK_BOX(bot), btn_exit, FALSE, FALSE, 0);

    /* ── Start ── */
    pthread_t tid;
    pthread_create(&tid, NULL, reader_thread, NULL);
    pthread_detach(tid);

    g_timeout_add(100, update_ui, NULL);

    gtk_widget_show_all(win);
    gtk_main();

    g_running = 0;
    free(g_avg_buf);
    return 0;
}



