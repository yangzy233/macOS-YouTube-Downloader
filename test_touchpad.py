#!/usr/bin/env python3
"""
test_touchpad.py — macOS 真机触控板手势实时诊断脚本
在终端实时输出触控板/滚轮触发的真实事件名称、delta 读数与计算步长，供真机实测诊断。
"""
import sys
import time
import customtkinter as ctk

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.title("macOS 触控板滚轮手势实时诊断")
app.geometry("620x540")

# 顶部提示与状态显示
header = ctk.CTkFrame(app, fg_color="transparent")
header.pack(fill="x", padx=16, pady=(14, 8))

ctk.CTkLabel(
    header,
    text="macOS 触控板真机手势实时诊断",
    font=ctk.CTkFont(size=16, weight="bold"),
    text_color="#FFFFFF",
).pack(anchor="w")

ctk.CTkLabel(
    header,
    text="请在下方卡片区域内，用双指在 MacBook 触控板上轻滑或慢划。终端将实时捕获并打印底层事件：",
    font=ctk.CTkFont(size=12),
    text_color="#8E8E93",
).pack(anchor="w", pady=(2, 4))

status_badge = ctk.CTkLabel(
    header,
    text="等待手势输入中...",
    font=ctk.CTkFont(size=12, weight="bold"),
    fg_color="#2C2C2E",
    corner_radius=6,
    padx=10,
    pady=4,
)
status_badge.pack(anchor="w", pady=(2, 0))

# 任务队列模拟滚动列表框
scroll_frame = ctk.CTkScrollableFrame(app, height=360, corner_radius=10, fg_color="#1C1C1E")
scroll_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

# 禁用 CTk 的破坏性解绑后门
canvas = scroll_frame._parent_canvas
canvas.configure(yscrollincrement=1)
try:
    canvas.unbind("<Enter>")
    canvas.unbind("<Leave>")
except Exception:
    pass

# 添加 20 个测试卡片
for i in range(1, 21):
    card = ctk.CTkFrame(scroll_frame, corner_radius=8, fg_color="#252528", border_width=1, border_color="#333336")
    card.pack(fill="x", padx=6, pady=4)

    ctk.CTkLabel(
        card,
        text=f"测试任务卡片 #{i:02d} — 4K HDR 视频任务",
        font=ctk.CTkFont(size=13, weight="bold"),
        text_color="#FFFFFF",
    ).pack(side="left", padx=14, pady=12)

    ctk.CTkLabel(
        card,
        text="双指滑动测试区",
        font=ctk.CTkFont(size=11),
        text_color="#8E8E93",
    ).pack(side="right", padx=14, pady=12)

# 强制刷新 Canvas 的真实几何尺寸与 scrollregion
app.update_idletasks()
canvas.configure(scrollregion=canvas.bbox("all"))

event_counter = 0

def on_gesture(event, event_name: str, is_touchpad: bool = False):
    global event_counter
    event_counter += 1

    raw_delta = getattr(event, "delta", 0)
    ev_type = str(getattr(event, "type", ""))

    mx, my = app.winfo_pointerxy()
    qx = scroll_frame.winfo_rootx()
    qy = scroll_frame.winfo_rooty()
    qw = scroll_frame.winfo_width()
    qh = scroll_frame.winfo_height()

    is_inside = (qx <= mx <= qx + qw and qy <= my <= qy + qh)

    # 解码计算 step
    step = 0
    if is_touchpad or "Touchpad" in ev_type or ev_type == "39" or raw_delta > 1000 or raw_delta < -1000:
        raw = raw_delta & 0xFFFFFFFF
        delta_y = raw & 0xFFFF
        if delta_y >= 0x8000:
            delta_y -= 0x10000
        step = -1 * delta_y
    else:
        if raw_delta != 0:
            step = -1 * int(raw_delta) * 12

    # 执行真实平滑滚动
    if is_inside and step != 0:
        canvas.yview_scroll(step, "units")

    y_pos = canvas.yview()[0]
    log_msg = (
        f"[{event_counter:04d}] 事件: {event_name:<16} | ev.type: {ev_type:<3} | "
        f"raw_delta: {raw_delta:>8} | 计算step: {step:>5} | 区域内: {is_inside!s:<5} | "
        f"当前滚动位置: {y_pos:.4f}"
    )
    print(log_msg)
    sys.stdout.flush()

    status_badge.configure(
        text=f"捕获手势 #{event_counter}: {event_name} (raw_delta={raw_delta}, step={step})",
        fg_color="#007AFF",
    )

# 绑定所有可能的事件
app.bind_all("<MouseWheel>", lambda e: on_gesture(e, "<MouseWheel>", False), add="+")
try:
    app.bind_all("<TouchpadScroll>", lambda e: on_gesture(e, "<TouchpadScroll>", True), add="+")
    print("成功注册 <TouchpadScroll> 触控板手势全局监听")
except Exception as exc:
    print(f"注: 当前 Tk 环境无法注册 <TouchpadScroll>: {exc}")

app.bind_all("<Button-4>", lambda e: on_gesture(type("E", (), {"delta": 1, "type": "MouseWheel"})(), "<Button-4>", False), add="+")
app.bind_all("<Button-5>", lambda e: on_gesture(type("E", (), {"delta": -1, "type": "MouseWheel"})(), "<Button-5>", False), add="+")

print("=" * 80)
print("macOS 触控板手势实时诊断窗口已启动")
print("请将鼠标指针移动到弹出的窗口中，在卡片上用双指上下轻滑，终端会实时输出每一帧手势读数！")
print("=" * 80)
sys.stdout.flush()

if __name__ == "__main__":
    app.mainloop()
