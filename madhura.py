from Heema import *
from tinydb import TinyDB, Query
import datetime
from tkinter import ttk

import os
import pygame
pygame.mixer.init()

sound = pygame.mixer.Sound("universfield-new-notification-041-493473.mp3")
sound.set_volume(0.2)  # 0.0 to 1.0


# =============================
# CONFIG
# =============================

TEST_MODE = True

# =============================
# DATABASE
# =============================

db = TinyDB("madhura_sessions.json")
query = Query()

# =============================
# GLOBAL STATE
# =============================

current_focus = ""
session_minutes = 60
time_left = 0

return_streak = 0
drift_count = 0

pill_window = None
pill_timer_label = None
pill_focus_label = None

streak_label = None
drift_label = None




# =============================
# Notifcation timer and sounds
# =============================


notification_job = None
AUTO_NOTIFY_INTERVAL =  60*7 # 60 seconds 10 times, is 10 minutes.




# Track escalation level
notification_level = 0

# How many levels we allow
MAX_SOUND_LEVEL = 1


def play_escalation_sound():
    global notification_level

    # notification_level += 1
    # level = min(notification_level, MAX_SOUND_LEVEL)

    sound_file = os.path.join(
        os.path.dirname(__file__),
        "universfield-new-notification-041-493473.wav"
    )

    try:
        sound.play()
    except:
        pass


def reset_escalation_sound():
    global notification_level
    notification_level = 0

def reset_escalation_sound():
    global notification_level
    notification_level = 0

def schedule_repeating_notification():
    global notification_job

    # Cancel existing loop
    if notification_job:
        root.after_cancel(notification_job)
        notification_job = None

    # Only run if session is active
    if current_focus and time_left > 0:
        focus_notification()

        notification_job = root.after(
            AUTO_NOTIFY_INTERVAL * 1000,
            schedule_repeating_notification
        )


schedule_repeating_notification()




# =============================
# CLOCK
# =============================

def update_clock():
    clock_label.config(
        text=datetime.datetime.now().strftime("%H:%M:%S")
    )
    root.after(1000, update_clock)

# =============================
# COUNTDOWN (STABLE + SYNCED)
# =============================

def countdown():
    global time_left

    if current_focus and time_left > 0:
        time_left -= 1

    # Always compute display from current state
    mins = time_left // 60
    secs = time_left % 60
    formatted = f"{mins:02d}:{secs:02d}"

    timer_label.config(text=formatted)

    if pill_window and pill_timer_label:
        pill_timer_label.config(text=formatted)

    root.after(1000, countdown)

# =============================
# SESSION CONTROL
# =============================

def set_focus():
    global current_focus, time_left, session_minutes

    text_value = focus_input.get().strip()

    if not text_value:
        status_label.config(
            text="Add something to your task.",
            fg="#ff6b6b"
        )
        return


    session_minutes = int(minutes_selector_dropdown.get())
    time_left = session_minutes * 60

    current_focus = text_value
    focus_display.config(text=current_focus)

    status_label.config(
        text="Session active.",
        fg="#4CAF50"
    )

    focus_input.delete(0, END)

    schedule_repeating_notification()
    root.update()
    root.attributes("-topmost", 0)
    create_focus_pill()

def reset_session():
    global current_focus, time_left, notification_job

    if notification_job:
        root.after_cancel(notification_job)
        notification_job = None

    current_focus = ""
    time_left = 0
    focus_display.config(text="Current Session: Empty")
    timer_label.config(text="00:00")
    status_label.config(text="", fg="#888888")

    #root.attributes("-topmost", 1)


# =============================
# ALIGNMENT
# =============================

def aligned():
    global return_streak
    return_streak += 1
    if streak_label:
        streak_label.config(text=f"Streak {return_streak}")

    root.withdraw()
    create_focus_pill()

def drifted():
    global drift_count
    drift_count += 1
    if drift_label:
        drift_label.config(text=f"Drifts {drift_count}")

    root.withdraw()
    create_focus_pill()

# =============================
# NOTIFICATION (TEST MODE)
# =============================

def focus_notification():
    if not current_focus:
        status_label.config(
            text="Start a session first.",
            fg="#ff6b6b"
        )
        return

    elapsed = (session_minutes * 60 - time_left) // 60
    remaining = time_left // 60

    notif = create_window_with_no_title_bar(bg="#1f1f1f")
    notif.attributes("-topmost", 1)
    make_rounded(notif)

    width = 320
    height = 150

    sw = notif.winfo_screenwidth()
    sh = notif.winfo_screenheight()

    notif.geometry(f"{width}x{height}+{sw-width-40}+{sh-height-60}")

    # ---- Main Container ----
    content = frame(notif, bg="#1f1f1f")
    content.pack(fill=BOTH, expand=True, padx=20, pady=18)

    # # ---- App Name ----
    # label(content,
    #       text="Madhura",
    #       font=("Segoe UI", 6),
    #       fg="#cccccc",
    #       bg="#1f1f1f").pack(anchor="w")

    # ---- Focus Title ----
    label(content,
          text=current_focus,
          font=("Segoe UI", 11),
          fg="#bbbbbb",
          bg="#1f1f1f").pack(anchor="w", pady=(4, 10))

    # ---- Progress Info ----
    label(content,
          text=f"{elapsed} min done · {remaining} min left",
          font=("Segoe UI", 11),
          fg="#e0e0e0",
          bg="#1f1f1f").pack(anchor="w", pady=(0, 16))

    # ---- Buttons Row ----
    row = frame(content, bg="#1f1f1f")
    row.pack(anchor="e")

    btn1 = button(row,
                   text="Focused",
                   command=lambda: (aligned(), notif.destroy()),
                   font=("Segoe UI", 9))
    btn1.config(padx=10, pady=4)
    btn1.pack(side=LEFT, padx=(0, 8))

    btn2 = button(row,
                   text="Focusing back",
                   command=lambda: (drifted(), notif.destroy()),
                   font=("Segoe UI", 9))
    btn2.config(padx=10, pady=4)
    btn2.pack(side=LEFT)

    play_escalation_sound()

    # Optional: auto close after 15 sec
    # notif.after(15000, notif.destroy)

# =============================
# SUMMARY
# =============================

def show_today_summary():
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    sessions = db.search(query.date == today)

    total_minutes = sum(s.get("completed_minutes", 0) for s in sessions)

    summary_window = create_window(text="Summary")
    make_rounded(summary_window)
    apply_theme(summary_window, light_mode)

    top_bar = frame(summary_window)
    top_bar.pack(fill=X, padx=15, pady=12)

    label_button(top_bar,
                 fg="white",
                 text="←",
                 command=summary_window.destroy,
                 font=("Segoe UI", 17)).pack(side=LEFT)

    main = frame(summary_window)
    main.pack(expand=True, padx=40, pady=30)

    label(main,
          text="Today's Focus",
          font=("Segoe UI", 20)).pack(pady=(0, 20))

    label(main,
          text=f"Total Focused: {total_minutes} minutes",
          font=("Segoe UI", 15),
          fg="#bbbbbb").pack()



#==============================
#HIDE WINDOW FUNCTION
#==============================

def hide_root_window():
    root.attributes("-topmost",0)
    root.withdraw()
    create_focus_pill()


# =============================
# PILL (SINGLE SURFACE + STABLE)
# =============================

def destroy_pill():
    global pill_window
    if pill_window:
        pill_window.destroy()
        pill_window = None

def create_focus_pill():
    global pill_window, pill_timer_label, pill_focus_label

    if pill_window:
        return

    pill_window = create_window_with_no_title_bar(bg="#202020")
    pill_window.attributes("-topmost", 1)
    make_rounded(pill_window)

    height = 34
    base_width = 120

    sw = pill_window.winfo_screenwidth()
    sh = pill_window.winfo_screenheight()

    pill_window.geometry(f"{base_width}x{height}+{sw-170}+{sh-100}")

    pill_timer_label = label(
        pill_window,
        text=timer_label.cget("text"),
        font=("Segoe UI", 10),
        fg="white",
        bg="#202020"
    )
    pill_timer_label.pack(side=LEFT, padx=(12, 4), pady=6)

    pill_focus_label = label(
        pill_window,
        text="",
        font=("Segoe UI", 9),
        fg="#bbbbbb",
        bg="#202020"
    )
    pill_focus_label.pack(side=LEFT, padx=(4, 12), pady=6)

    hover_job = None

    def resize_to_content():
        pill_window.update_idletasks()
        new_width = pill_timer_label.winfo_reqwidth() + \
                    pill_focus_label.winfo_reqwidth() + 32
        x = pill_window.winfo_x()
        y = pill_window.winfo_y()
        pill_window.geometry(f"{new_width}x{height}+{x}+{y}")

    def expand():
        if current_focus:
            pill_focus_label.config(text=f"| {current_focus}")
            resize_to_content()

    def collapse():
        pill_focus_label.config(text="")
        resize_to_content()

    def on_enter(e):
        nonlocal hover_job
        hover_job = pill_window.after(180, expand)

    def on_leave(e):
        nonlocal hover_job
        if hover_job:
            pill_window.after_cancel(hover_job)
        pill_window.after(180, collapse)

    pill_window.bind("<Enter>", on_enter)
    pill_window.bind("<Leave>", on_leave)

    def start_drag(event):
        pill_window.drag_x = event.x
        pill_window.drag_y = event.y

    def do_drag(event):
        new_x = event.x_root - pill_window.drag_x
        new_y = event.y_root - pill_window.drag_y

        width = pill_window.winfo_width()
        height_local = pill_window.winfo_height()

        new_x = max(0, min(new_x, sw - width))
        new_y = max(0, min(new_y, sh - height_local))

        pill_window.geometry(f"+{new_x}+{new_y}")

    pill_window.bind("<Button-1>", start_drag)
    pill_window.bind("<B1-Motion>", do_drag)

    pill_window.bind("<Double-Button-1>",
                     lambda e: (root.deiconify()))

# =============================
# UI BUILD
# =============================

root = create_window_with_no_title_bar(text="Madhura")

zen_mode(root)
apply_theme(root, dark_mode)
make_rounded(root)

main_frame = frame(root)
main_frame.pack(expand=True, padx=70, pady=55)

header_frame = frame(main_frame)
header_frame.pack(fill=X)

label(header_frame,
      text="Madhura",
      fg="#888888",
      font=("Segoe UI", 26)).pack(side=LEFT)

clock_label = label(header_frame,
                    text="",
                    font=("Segoe UI", 14),
                    fg="#888888")
clock_label.pack(side=RIGHT)

timer_label = label(main_frame,
                    text="00:00",
                    font=("Segoe UI", 32))
timer_label.pack(pady=(35, 12))

focus_display = label(main_frame,
                      text="Current Session: Empty",
                      font=("Segoe UI", 18),
                      fg="#bbbbbb")
focus_display.pack(pady=(0, 25))

label(main_frame,
      text="Plan your next hour",
      font=("Segoe UI", 20),
      fg="#cccccc").pack(pady=(5, 6))



focus_input = Entry(

    main_frame,
    font=("Segoe UI", 20),
    bg="#ffffff",
    fg="#000000",
    insertbackground="#000000",
    bd=0
)
focus_input.pack(ipadx=80, ipady=9, pady=(0, 22))

focus_input.focus()

style = ttk.Style()
style.theme_use("default")

style.configure("TCombobox",
                fieldbackground="#202020",
                background="#202020",
                foreground="white")

root.option_add("*TCombobox*Listbox.background", "#202020")
root.option_add("*TCombobox*Listbox.foreground", "white")

# --- Hours Selector Row ---

hours_row = frame(main_frame)
hours_row.pack(pady=(0, 29))

minutes_selector_dropdown = ttk.Combobox(
    hours_row,
    values=[10, 15, 30, 60,120,240],
    width=4,
    style="TCombobox"
)
minutes_selector_dropdown.set(60)
minutes_selector_dropdown.pack(side=LEFT)

minutes_selector_dropdown_label = label(
    hours_row,
    text="Minutes",
    font=("Segoe UI", 13),
    fg="#bbbbbb"
)
minutes_selector_dropdown_label.pack(side=LEFT, padx=(8, 0))

button_row = frame(main_frame)
button_row.pack(pady=10)

button(button_row, text="Lock", command=set_focus).pack(side=LEFT, padx=10)
button(button_row, text="Reset", command=reset_session).pack(side=LEFT, padx=10)
button(button_row, text="Summary", command=show_today_summary).pack(side=LEFT, padx=10)


if TEST_MODE:
    button(button_row, text="Notify", command=focus_notification).pack(side=LEFT, padx=10)
    button(button_row, text="Pill", command=create_focus_pill).pack(side=LEFT, padx=10)



button(button_row, text="Hide", command=hide_root_window).pack(side=LEFT, padx=10)

if TEST_MODE:
    stats_frame = frame(main_frame)
    stats_frame.pack(pady=(25, 0))

    streak_label = label(stats_frame,
                         text="Streak 0",
                         fg="#888888",
                         font=("Segoe UI", 13))
    streak_label.pack(side=LEFT, padx=15)

    drift_label = label(stats_frame,
                        text="Drifts 0",
                        fg="#888888",
                        font=("Segoe UI", 13))
    drift_label.pack(side=LEFT, padx=15)

status_label = label(main_frame,
                     text="",
                     font=("Segoe UI", 12),
                     fg="#888888")
status_label.pack(pady=18)





def ensure_activity_visibility():
    global current_focus, time_left

    session_active = bool(current_focus) and time_left > 0

    print(f"is session active? {session_active}")


    if session_active == False:
        # Bring main window forward and maximize

        # root.deiconify()

        is_window_topmost = bool(root.attributes("-topmost"))
        print(f"is window topmost: {is_window_topmost}")
        if (is_window_topmost == False and session_active == False):
            print("is topmost ? false!")
            root.attributes("-topmost", 1)
            root.deiconify()

            status_label.config(
                text="Create a new session.",
                fg="#4CAF50"
            )

        # Optional: remove topmost after short delay
        # root.after(1500, lambda: root.attributes("-topmost", 0))
    else:
        # If session active, behave normally


        root.attributes("-topmost", 0)


    # if(time_left==0):
    #
    #
    #     print("time left is zero")




def reset_session():
    global current_focus, time_left, notification_job

    if notification_job:
        root.after_cancel(notification_job)
        notification_job = None

    current_focus = ""
    time_left = 0
    focus_display.config(text="Current Session: Empty")
    timer_label.config(text="00:00")
    status_label.config(text="", fg="#888888")
    status_label.update()
    root.update()

    # ensure_activity_visibility()

def activity_watchdog():
    print("checking")
    ensure_activity_visibility()
      # check every 1 sec
    root.after(10000, activity_watchdog)






def bind_root_guard():


    def on_minimize(event):
        # Detect minimize specifically

        session_active = bool(current_focus) and time_left > 0
        global pill_window
        if(session_active==True):
            print("is called on minimize")
            create_focus_pill()
            # root.withdraw()





    # def on_focus_out(event):
    #     # Avoid false trigger when interacting with child windows
    #     if root.state() == "normal":
    #         send_to_pill()

    def on_close():
        # Override destroy — convert to pill instead
        create_focus_pill()

    # Bind events
    root.bind("<Unmap>", on_minimize)       # minimize trigger
    # root.bind("<FocusOut>", on_focus_out)   # focus lost
    # root.protocol("WM_DELETE_WINDOW", on_close)




root.after(1000, activity_watchdog)


bind_root_guard()

def show_pill_if_needed():
    if current_focus and time_left > 0:
        create_focus_pill()
        root.withdraw()


# def monitor_visibility(event=None):
#     is_visible = root.state() == "normal"
#     has_focus = root.focus_displayof() is not None
#
#     if not (is_visible and has_focus):
#         show_pill_if_needed()
#
#






# root.bind("<FocusOut>", monitor_visibility)
# root.bind("<Unmap>", monitor_visibility)   # minimize trigger

update_clock()
countdown()





root.mainloop()