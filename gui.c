/*
 * gui.c — Weighing Terminal GUI
 *
 * Connects to the weighing terminal over TCP, displays live weight
 * with 3 modes.
 *
 * Compile:  gcc -o gui gui.c $(pkg-config --cflags --libs gtk+-3.0) -lpthread -lm
 * Run:      ./gui
 */

#include <gtk/gtk.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>

/* ── Config ────────────────────────────────────────────────────────────────── */
#define TCP_HOST   "192.168.0.1"
#define TCP_PORT   1702
#define LOCAL_IP   "192.168.0.100"
#define HISTORY    100
#define MODE_FILE  "/tmp/weight_mode"
/* ─────────────────────────────────────────────────────────────────────────── */

/* ── Forward declarations ────────────────────────────────────────────────────*/
static void apply_css(GtkWidget *w, const char *css);

/* ── App state ───────────────────────────────────────────────────────────────*/
typedef struct {
    /* Network */
    int          sock;
    volatile int running;
    pthread_t    reader_thread;

    /* Data */
    int          mode;          /* 1, 2, 3 */
    double       history[HISTORY];
    int          hist_head;
    int          hist_count;
    long         packet_no;
    pthread_mutex_t lock;

    /* GTK widgets */
    GtkWidget   *weight_label;
    GtkWidget   *unit_label;
    GtkWidget   *mode_label;
    GtkWidget   *min_label;
    GtkWidget   *max_label;
    GtkWidget   *mean_label;
    GtkWidget   *pkt_label;
    GtkWidget   *status_label;
    GtkWidget   *log_buffer_view;
    GtkTextBuffer *log_buf;
    GtkWidget   *mode_btns[3];
} App;

static App app;

/* ── Ring buffer helpers ─────────────────────────────────────────────────────*/
static void hist_push(double v) {
    app.history[app.hist_head] = v;
    app.hist_head = (app.hist_head + 1) % HISTORY;
    if (app.hist_count < HISTORY) app.hist_count++;
}

static double hist_min(void) {
    double m = app.history[0];
    for (int i = 1; i < app.hist_count; i++)
        if (app.history[i] < m) m = app.history[i];
    return m;
}

static double hist_max(void) {
    double m = app.history[0];
    for (int i = 1; i < app.hist_count; i++)
        if (app.history[i] > m) m = app.history[i];
    return m;
}

static double hist_mean(void) {
    double s = 0;
    for (int i = 0; i < app.hist_count; i++) s += app.history[i];
    return app.hist_count ? s / app.hist_count : 0.0;
}

/* ── Mode helpers ────────────────────────────────────────────────────────────*/
static const char *mode_unit(int m)  { return (m == 1) ? "kg" : "ton"; }
static const char *mode_desc(int m) {
    switch (m) {
        case 1: return "Mode 1 — Real-time kg";
        case 2: return "Mode 2 — 1 kg = 1 ton";
        case 3: return "Mode 3 — 1 kg = 2 ton";
    }
    return "";
}
static double mode_convert(int m, double kg) {
    return (m == 3) ? kg * 2.0 : kg;
}

/* ── Timestamp ───────────────────────────────────────────────────────────────*/
static void ts_short(char *buf, size_t n) {
    struct timeval tv; gettimeofday(&tv, NULL);
    struct tm *t = localtime(&tv.tv_sec);
    snprintf(buf, n, "%02d:%02d:%02d", t->tm_hour, t->tm_min, t->tm_sec);
}

/* ── GTK update — runs on main thread via g_idle_add ─────────────────────── */
typedef struct { double weight_kg; } UpdateData;

static gboolean do_update(gpointer data) {
    UpdateData *ud = (UpdateData *)data;
    double weight_kg = ud->weight_kg;
    free(ud);

    pthread_mutex_lock(&app.lock);
    int mode = app.mode;
    pthread_mutex_unlock(&app.lock);

    double display_val = mode_convert(mode, weight_kg);
    const char *unit   = mode_unit(mode);

    pthread_mutex_lock(&app.lock);
    hist_push(display_val);
    app.packet_no++;
    long pno = app.packet_no;
    double w_min  = hist_min();
    double w_max  = hist_max();
    double w_mean = hist_mean();
    pthread_mutex_unlock(&app.lock);2];
    snprintf(buf, sizeof(buf), "%.2f", display_val);
    gtk_label_set_text(GTK_LABEL(app.weight_label), buf);
    gtk_label_set_text(GTK_LABEL(app.unit_label), unit);

    /* Mode */
    gtk_label_set_text(GTK_LABEL(app.mode_label), mode_desc(mode));

    /* Stats */
    snprintf(buf, sizeof(buf), "min\n%.2f %s", w_min, unit);
    gtk_label_set_text(GTK_LABEL(app.min_label), buf);
    snprintf(buf, sizeof(buf), "max\n%.2f %s", w_max, unit);
    gtk_label_set_text(GTK_LABEL(app.max_label), buf);
    snprintf(buf, sizeof(buf), "mean\n%.2f %s", w_mean, unit);
    gtk_label_set_text(GTK_LABEL(app.mean_label), buf);
    snprintf(buf, sizeof(buf), "packets\n%ld", pno);
    gtk_label_set_text(GTK_LABEL(app.pkt_label), buf);

    /* Log */
    char ts2[16]; ts_short(ts2, sizeof(ts2));
    char entry[64];
    snprintf(entry, sizeof(entry), "[%s]  #%-6ld  %8.2f %s\n",
             ts2, pno, display_val, unit);
    GtkTextIter iter;
    gtk_text_buffer_get_end_iter(app.log_buf, &iter);
    gtk_text_buffer_insert(app.log_buf, &iter, entry, -1);

    /* Keep last 200 lines */
    int lines = gtk_text_buffer_get_line_count(app.log_buf);
    if (lines > 200) {
        GtkTextIter start, end;
        gtk_text_buffer_get_start_iter(app.log_buf, &start);
        gtk_text_buffer_get_iter_at_line(app.log_buf, &end, lines - 200);
        gtk_text_buffer_delete(app.log_buf, &start, &end);
    }
    /* Auto-scroll */
    gtk_text_buffer_get_end_iter(app.log_buf, &iter);
    GtkTextMark *mark = gtk_text_buffer_get_insert(app.log_buf);
    gtk_text_buffer_place_cursor(app.log_buf, &iter);
    gtk_text_view_scroll_mark_onscreen(GTK_TEXT_VIEW(app.log_buffer_view), mark);

    return G_SOURCE_REMOVE;
}

typedef struct { char msg[128]; gboolean connected; } StatusData;

static gboolean do_status(gpointer data) {
    StatusData *sd = (StatusData *)data;
    gtk_label_set_text(GTK_LABEL(app.status_label), sd->msg);

    const char *color = sd->connected ? "#00d4aa" : "#ff4757";
    char css[64];
    snprintf(css, sizeof(css), "label { color: %s; font-size: 10px; }", color);
    apply_css(app.status_label, css);

    free(sd);
    return G_SOURCE_REMOVE;
}

/* ── Reader thread ───────────────────────────────────────────────────────────*/
static void post_status(const char *msg, gboolean connected) {
    StatusData *sd = malloc(sizeof(StatusData));
    strncpy(sd->msg, msg, sizeof(sd->msg)-1);
    sd->connected = connected;
    g_idle_add(do_status, sd);
}

static void *reader_thread(void *arg) {
    (void)arg;

    /* TCP connect */
    int sock = socket(AF_INET, SOCK_STREAM, 0);
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
        post_status("Connection failed", FALSE);
        return NULL;
    }

    app.sock = sock;
    char status[64];
    snprintf(status, sizeof(status), "Connected  %s:%d", TCP_HOST, TCP_PORT);
    post_status(status, TRUE);

    char buf[4096];
    int  buf_len = 0;

    while (app.running) {
        ssize_t n = recv(sock, buf + buf_len, sizeof(buf) - buf_len - 1, 0);
        if (n <= 0) {
            if (app.running) post_status("Connection closed", FALSE);
            break;
        }
        buf_len += n;
        buf[buf_len] = '\0';

        char *line_start = buf;
        char *newline;
        while ((newline = strchr(line_start, '\n')) != NULL) {
            *newline = '\0';
            char *p = line_start;
            while (*p == ' ' || *p == '\r' || *p == '\t') p++;

            double weight_kg = 0.0;
            if (*p != '\0' && sscanf(p, "%lf", &weight_kg) == 1) {
                UpdateData *ud = malloc(sizeof(UpdateData));
                ud->weight_kg = weight_kg;
                g_idle_add(do_update, ud);
            }
            line_start = newline + 1;
        }
        buf_len = (int)(buf + buf_len - line_start);
        memmove(buf, line_start, buf_len);
    }

    close(sock);
    return NULL;
}

/* ── Mode button callbacks ───────────────────────────────────────────────────*/
static void highlight_mode(int m) {
    const char *css_active =
        "button { background: #00d4aa; color: #1a1a2e; font-weight: bold; border-radius: 4px; }";
    const char *css_normal =
        "button { background: #0f3460; color: #e0e0e0; font-weight: bold; border-radius: 4px; }";

    for (int i = 0; i < 3; i++) {
        GtkCssProvider *prov = gtk_css_provider_new();
        gtk_css_provider_load_from_data(prov,
            (i + 1 == m) ? css_active : css_normal, -1, NULL);
        gtk_style_context_add_provider(
            gtk_widget_get_style_context(app.mode_btns[i]),
            GTK_STYLE_PROVIDER(prov),
            GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
        g_object_unref(prov);
    }
}

static void set_mode(int m) {
    pthread_mutex_lock(&app.lock);
    app.mode = m; app.hist_count = 0; app.hist_head = 0;
    pthread_mutex_unlock(&app.lock);
    highlight_mode(m);
    FILE *f = fopen(MODE_FILE, "w");
    if (f) { fprintf(f, "%d\n", m); fclose(f); }
}

static void on_mode1(GtkButton *b, gpointer d) { (void)b; (void)d; set_mode(1); }
static void on_mode2(GtkButton *b, gpointer d) { (void)b; (void)d; set_mode(2); }
static void on_mode3(GtkButton *b, gpointer d) { (void)b; (void)d; set_mode(3); }

/* ── Key press ───────────────────────────────────────────────────────────────*/
static gboolean on_key(GtkWidget *w, GdkEventKey *ev, gpointer d) {
    (void)w; (void)d;
    if (ev->keyval == GDK_KEY_1) { on_mode1(NULL, NULL); return TRUE; }
    if (ev->keyval == GDK_KEY_2) { on_mode2(NULL, NULL); return TRUE; }
    if (ev->keyval == GDK_KEY_3) { on_mode3(NULL, NULL); return TRUE; }
    return FALSE;
}

/* ── Window close ────────────────────────────────────────────────────────────*/
static void on_destroy(GtkWidget *w, gpointer d) {
    (void)w; (void)d;
    app.running = 0;
    if (app.sock > 0) shutdown(app.sock, SHUT_RDWR);
    pthread_join(app.reader_thread, NULL);
    gtk_main_quit();
}

/* ── CSS helper ──────────────────────────────────────────────────────────────*/
static void apply_css(GtkWidget *w, const char *css) {
    GtkCssProvider *p = gtk_css_provider_new();
    gtk_css_provider_load_from_data(p, css, -1, NULL);
    gtk_style_context_add_provider(gtk_widget_get_style_context(w),
        GTK_STYLE_PROVIDER(p), GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    g_object_unref(p);
}

/* ── Stat card ───────────────────────────────────────────────────────────────*/
static GtkWidget *make_stat_card(const char *init_text, const char *color,
                                  GtkWidget **label_out) {
    GtkWidget *frame = gtk_frame_new(NULL);
    apply_css(frame,
        "frame { background: #16213e; border-radius: 6px; border: none; padding: 8px; }");

    GtkWidget *lbl = gtk_label_new(init_text);
    char css[128];
    snprintf(css, sizeof(css),
        "label { color: %s; font-size: 13px; font-weight: bold; }", color);
    apply_css(lbl, css);
    gtk_label_set_justify(GTK_LABEL(lbl), GTK_JUSTIFY_CENTER);
    gtk_container_add(GTK_CONTAINER(frame), lbl);
    if (label_out) *label_out = lbl;
    return frame;
}

/* ── Build UI ────────────────────────────────────────────────────────────────*/
static void build_ui(GtkApplication *gapp) {
    GtkWidget *window = gtk_application_window_new(gapp);
    gtk_window_set_title(GTK_WINDOW(window), "Weighing Terminal");
    gtk_window_set_default_size(GTK_WINDOW(window), 480, 620);
    gtk_window_set_resizable(GTK_WINDOW(window), FALSE);
    apply_css(window, "window { background-color: #1a1a2e; }");
    g_signal_connect(window, "destroy", G_CALLBACK(on_destroy), NULL);
    g_signal_connect(window, "key-press-event", G_CALLBACK(on_key), NULL);

    GtkWidget *vbox = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_container_add(GTK_CONTAINER(window), vbox);

    /* ── Title */
    GtkWidget *title = gtk_label_new("WEIGHING TERMINAL");
    apply_css(title,
        "label { background: #0f3460; color: #e0e0e0; font-size: 14px;"
        " font-weight: bold; padding: 12px; }");
    gtk_box_pack_start(GTK_BOX(vbox), title, FALSE, FALSE, 0);

    /* ── Status bar */
    GtkWidget *status_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    apply_css(status_row, "box { background: #16213e; padding: 6px 12px; }");
    gtk_box_pack_start(GTK_BOX(vbox), status_row, FALSE, FALSE, 4);

    app.status_label = gtk_label_new("Connecting...");
    apply_css(app.status_label, "label { color: #ffa502; font-size: 10px; }");
    gtk_box_pack_start(GTK_BOX(status_row), app.status_label, FALSE, FALSE, 0);

    /* ── Mode buttons */
    GtkWidget *mode_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    apply_css(mode_box, "box { padding: 8px 10px 4px 10px; }");
    gtk_box_pack_start(GTK_BOX(vbox), mode_box, FALSE, FALSE, 0);

    GtkWidget *mode_lbl = gtk_label_new("MODE");
    apply_css(mode_lbl, "label { color: #7a7a9a; font-size: 9px; font-weight: bold; }");
    gtk_widget_set_halign(mode_lbl, GTK_ALIGN_START);
    gtk_box_pack_start(GTK_BOX(mode_box), mode_lbl, FALSE, FALSE, 0);

    GtkWidget *btn_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_box_pack_start(GTK_BOX(mode_box), btn_row, FALSE, FALSE, 0);

    const char *btn_labels[] = { "[1] Real-time kg", "[2] 1 kg = 1 ton", "[3] 1 kg = 2 ton" };
    GCallback   btn_cbs[]    = { G_CALLBACK(on_mode1), G_CALLBACK(on_mode2), G_CALLBACK(on_mode3) };
    for (int i = 0; i < 3; i++) {
        GtkWidget *btn = gtk_button_new_with_label(btn_labels[i]);
        app.mode_btns[i] = btn;
        g_signal_connect(btn, "clicked", btn_cbs[i], NULL);
        gtk_box_pack_start(GTK_BOX(btn_row), btn, TRUE, TRUE, 0);
    }
    highlight_mode(1);

    /* ── Weight display */
    GtkWidget *weight_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    apply_css(weight_card,
        "box { background: #16213e; border-radius: 8px; padding: 20px; margin: 6px 10px; }");
    gtk_box_pack_start(GTK_BOX(vbox), weight_card, FALSE, FALSE, 0);

    GtkWidget *wlbl_title = gtk_label_new("CURRENT WEIGHT");
    apply_css(wlbl_title, "label { color: #7a7a9a; font-size: 10px; }");
    gtk_box_pack_start(GTK_BOX(weight_card), wlbl_title, FALSE, FALSE, 0);

    app.weight_label = gtk_label_new("---");
    apply_css(app.weight_label,
        "label { color: #00d4aa; font-size: 60px; font-weight: bold; }");
    gtk_box_pack_start(GTK_BOX(weight_card), app.weight_label, FALSE, FALSE, 0);

    app.unit_label = gtk_label_new("kg");
    apply_css(app.unit_label, "label { color: #7a7a9a; font-size: 16px; }");
    gtk_box_pack_start(GTK_BOX(weight_card), app.unit_label, FALSE, FALSE, 0);

    app.mode_label = gtk_label_new("Mode 1 — Real-time kg");
    apply_css(app.mode_label, "label { color: #7a7a9a; font-size: 10px; padding-top: 4px; }");
    gtk_box_pack_start(GTK_BOX(weight_card), app.mode_label, FALSE, FALSE, 0);

    /* ── Stats row */
    GtkWidget *stats_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    apply_css(stats_row, "box { padding: 0 10px; }");
    gtk_box_pack_start(GTK_BOX(vbox), stats_row, FALSE, FALSE, 6);

    gtk_box_pack_start(GTK_BOX(stats_row),
        make_stat_card("min\n---",     "#e0e0e0", &app.min_label),  TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(stats_row),
        make_stat_card("max\n---",     "#ff4757", &app.max_label),  TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(stats_row),
        make_stat_card("mean\n---",    "#ffa502", &app.mean_label), TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(stats_row),
        make_stat_card("packets\n0",   "#7a7a9a", &app.pkt_label),  TRUE, TRUE, 0);

    /* ── Log view */
    GtkWidget *log_frame = gtk_frame_new(NULL);
    apply_css(log_frame,
        "frame { background: #16213e; border-radius: 6px;"
        " border: none; margin: 6px 10px; padding: 6px; }");
    gtk_box_pack_start(GTK_BOX(vbox), log_frame, TRUE, TRUE, 0);

    GtkWidget *log_vbox = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    gtk_container_add(GTK_CONTAINER(log_frame), log_vbox);

    GtkWidget *log_title = gtk_label_new("RECENT READINGS");
    apply_css(log_title, "label { color: #7a7a9a; font-size: 9px; font-weight: bold; }");
    gtk_widget_set_halign(log_title, GTK_ALIGN_START);
    gtk_box_pack_start(GTK_BOX(log_vbox), log_title, FALSE, FALSE, 0);

    app.log_buf = gtk_text_buffer_new(NULL);
    app.log_buffer_view = gtk_text_view_new_with_buffer(app.log_buf);
    gtk_text_view_set_editable(GTK_TEXT_VIEW(app.log_buffer_view), FALSE);
    gtk_text_view_set_cursor_visible(GTK_TEXT_VIEW(app.log_buffer_view), FALSE);
    apply_css(app.log_buffer_view,
        "textview { background: #0f3460; color: #00d4aa;"
        " font-family: monospace; font-size: 10px; }");

    GtkWidget *scroll = gtk_scrolled_window_new(NULL, NULL);
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scroll),
        GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
    gtk_widget_set_size_request(scroll, -1, 120);
    gtk_container_add(GTK_CONTAINER(scroll), app.log_buffer_view);
    gtk_box_pack_start(GTK_BOX(log_vbox), scroll, TRUE, TRUE, 0);

    /* ── Bottom hint */
    GtkWidget *hint = gtk_label_new("Keyboard: 1 / 2 / 3 to switch mode");
    apply_css(hint, "label { color: #7a7a9a; font-size: 9px; padding: 6px; }");
    gtk_box_pack_start(GTK_BOX(vbox), hint, FALSE, FALSE, 0);

    gtk_widget_show_all(window);
}

/* ── App activate ────────────────────────────────────────────────────────────*/
static void on_activate(GtkApplication *gapp, gpointer d) {
    (void)d;

    /* Init app state */
    memset(&app, 0, sizeof(app));
    app.mode    = 1;
    app.running = 1;
    pthread_mutex_init(&app.lock, NULL);

    /* Write default mode to file */
    { FILE *f = fopen(MODE_FILE, "w"); if (f) { fprintf(f, "1\n"); fclose(f); } }

    build_ui(gapp);

    pthread_create(&app.reader_thread, NULL, reader_thread, NULL);
}

/* ── Main ────────────────────────────────────────────────────────────────────*/
int main(int argc, char **argv) {
    GtkApplication *gapp = gtk_application_new(
        "com.weighing.terminal", G_APPLICATION_DEFAULT_FLAGS);
    g_signal_connect(gapp, "activate", G_CALLBACK(on_activate), NULL);
    int status = g_application_run(G_APPLICATION(gapp), argc, argv);
    g_object_unref(gapp);
    return status;
}
