import json
import os
import tempfile
import time

import google.generativeai as genai
import streamlit as st
from moviepy import VideoFileClip, concatenate_videoclips
from proglog import ProgressBarLogger

# --- 設定頁面資訊 ---
st.set_page_config(page_title="Gemini AI 影片剪輯師", page_icon="🎬", layout="centered")


# --- 自定義 Logger (連接 Streamlit 進度條與狀態文字) ---
class StreamlitLogger(ProgressBarLogger):
    def __init__(self, progress_bar, status_text):
        super().__init__()
        self.progress_bar = progress_bar
        self.status_text = status_text

    def bars_callback(self, bar, attr, value, old_value=None):
        super().bars_callback(bar, attr, value, old_value)
        # 't' 代表時間軸的渲染進度，這是 MoviePy 最主要的進度指標
        if bar == "t" and "total" in self.bars[bar]:
            total = self.bars[bar]["total"]
            if total > 0:
                p = min(value / total, 1.0)
                # 更新進度條
                self.progress_bar.progress(p)
                # 更新文字顯示百分比
                percentage = int(p * 100)
                self.status_text.markdown(
                    f"**🎬 影片渲染中... {percentage}%** (正在處理畫面與音訊編碼)"
                )


# --- 輔助函式：等待 Gemini 檔案處理 ---
def wait_for_files_active(files):
    with st.spinner("⏳ 正在等待 Google AI 伺服器處理影片檔案..."):
        for name in (file.name for file in files):
            file = genai.get_file(name)
            while file.state.name == "PROCESSING":
                time.sleep(5)
                file = genai.get_file(name)
            if file.state.name != "ACTIVE":
                st.error(f"檔案 {file.name} 處理失敗。")
                return False
    return True


# --- 介面設計 ---
st.title("🧠 Google Gemini AI 影片剪輯師")
st.markdown("上傳影片、設定目標，剩下的交給 AI！")
st.markdown("---")

# 側邊欄：API Key 設定
with st.sidebar:
    st.header("🔑 設定")
    api_key = st.text_input(
        "Google API Key", type="password", placeholder="貼上你的 AI Studio Key"
    )
    st.caption("[如何取得 API Key?](https://aistudio.google.com/app/apikey)")
    st.markdown("---")
    st.info("💡 提示：越詳細的指令，AI 剪得越好。")

# 主畫面輸入區
col1, col2 = st.columns([1, 1])

with col1:
    uploaded_file = st.file_uploader("1. 上傳影片 (MP4/MOV)", type=["mp4", "mov"])

with col2:
    # 新增：目標片長設定
    target_duration = st.number_input(
        "2. 預計輸出片長 (秒)",
        min_value=10,
        value=60,
        step=10,
        help="AI 會嘗試剪輯到這個長度，允許 ±10 秒誤差",
    )

# 優化：使用 placeholder 讓點擊後不需要手動刪字
prompt_placeholder = (
    "例如：這是一段旅遊影片，請幫我剪輯出風景最漂亮、還有大家一起笑的片段。節奏要輕快。"
)
prompt_text = st.text_area(
    "3. 給剪輯師的指令 (點擊輸入)", placeholder=prompt_placeholder, height=100
)

# 新增：自訂檔名
output_filename = st.text_input(
    "4. 輸出檔案名稱", value="my_ai_video", placeholder="輸入檔名 (不需要打 .mp4)"
)

if st.button("🚀 開始 AI 智慧剪輯", type="primary", use_container_width=True):
    # 檢查必要欄位
    if not api_key:
        st.warning("⚠️ 請先在左側輸入 Google API Key")
        st.stop()

    if not uploaded_file:
        st.warning("⚠️ 請先上傳影片檔案")
        st.stop()

    if not prompt_text:
        # 如果使用者沒打字，使用預設提示，或者提醒他
        st.info("💡 你沒有輸入指令，將使用通用剪輯模式：挑選精彩片段。")
        prompt_text = "請幫我挑選影片中最精彩、畫面最穩定的片段。"

    # 處理檔名 (確保有 .mp4 後綴)
    if not output_filename.strip():
        output_filename = "gemini_cut"
    if not output_filename.endswith(".mp4"):
        final_filename = f"{output_filename}.mp4"
    else:
        final_filename = output_filename

    # --- 處理流程開始 ---
    st.toast("開始處理中...", icon="🤖")

    # 1. 設定 API
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        st.error(f"API Key 無效: {e}")
        st.stop()

    # 2. 儲存暫存檔
    tfile = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tfile.write(uploaded_file.read())
    temp_video_path = tfile.name
    tfile.close()

    # 初始化資源變數，避免 finally 區塊因變數未定義而報錯
    clip = None
    final_clip = None
    video_file = None

    try:
        # 使用 st.status 顯示多階段狀態
        status_box = st.status("正在啟動 AI 處理流程...", expanded=True)

        # 3. 上傳影片給 Gemini
        status_box.write("📤 **階段 1/4**: 正在將影片傳送給 Google Gemini...")
        video_file = genai.upload_file(path=temp_video_path)

        status_box.write("⏳ **階段 2/4**: 等待 AI 觀看並分析影片內容...")
        if not wait_for_files_active([video_file]):
            st.stop()

        # 4. 生成剪輯指令
        status_box.write("🧠 **階段 3/4**: AI 正在思考最佳片段 (根據您的指令與長度)...")

        # 動態調整 Prompt，加入時間限制
        prompt = f"""
        你是一個專業的影片剪輯師。
        請分析這段影片，並根據使用者的要求：「{prompt_text}」
        
        【重要限制】
        1. 目標總長度：大約 {target_duration} 秒 (允許 ±10 秒誤差)。
        2. 請挑選最符合描述的 3 到 8 個精華片段。
        
        請嚴格遵守以下 JSON 格式回傳，不要包含任何 Markdown 標記或 ```json 字樣：
        [
            {{"start": 開始秒數(float), "end": 結束秒數(float), "reason": "選擇原因"}}
        ]
        確保片段之間不重疊。
        """

        # 自動模型偵測與切換 (修正版)
        try:
            # 1. 取得所有可用模型
            available_models = [
                m.name
                for m in genai.list_models()
                if "generateContent" in m.supported_generation_methods
            ]

            # 2. 定義優先順序 (從新到舊，Flash 優先因為速度快且便宜)
            priority_list = [
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-1.5-flash",
                "gemini-flash",
                "gemini-2.5-pro",
                "gemini-2.0-pro",
                "gemini-1.5-pro",
                "gemini-pro",
            ]

            selected_model_name = None

            # 3. 依序匹配
            for keyword in priority_list:
                found = next((m for m in available_models if keyword in m), None)
                if found:
                    selected_model_name = found
                    break

            # 4. 如果都沒找到，選列表中的第一個
            if not selected_model_name and available_models:
                selected_model_name = available_models[0]

            if not selected_model_name:
                status_box.update(label="API 錯誤", state="error")
                st.error("❌ 無法找到任何可用的 Gemini 模型。請檢查 API Key 權限。")
                st.stop()

            status_box.write(f"🤖 使用模型: `{selected_model_name}`")

            # 5. 執行生成
            model = genai.GenerativeModel(model_name=selected_model_name)
            response = model.generate_content([video_file, prompt])

        except Exception as e:
            status_box.update(label="模型執行失敗", state="error")
            st.error(f"❌ 模型執行發生錯誤: {e}")
            st.stop()

        # 5. 解析結果
        try:
            clean_json = response.text.replace("```json", "").replace("```", "").strip()
            timestamps = json.loads(clean_json)

            status_box.write("📋 **AI 剪輯決策**：")
            total_cut_duration = 0
            for t in timestamps:
                duration = float(t["end"]) - float(t["start"])
                total_cut_duration += duration
                status_box.write(
                    f"- `{t['start']}s` ~ `{t['end']}s` ({duration:.1f}s): {t.get('reason', '精華片段')}"
                )

            status_box.write(f"⏱️ 預計總片長: **{total_cut_duration:.1f} 秒**")

        except json.JSONDecodeError:
            status_box.update(label="AI 回傳格式錯誤", state="error")
            st.error("AI 思考當機了，請重試一次。")
            st.stop()

        # 6. 實體剪輯
        status_box.update(
            label="🎬 **階段 4/4**: 正在渲染影片 (這可能需要一點時間)...",
            state="running",
        )

        # 建立進度條容器
        progress_bar = st.progress(0)
        status_text = st.empty()  # 用來顯示百分比文字

        clip = VideoFileClip(temp_video_path)
        subclips = []

        for t in timestamps:
            start = max(0, float(t["start"]))
            end = min(clip.duration, float(t["end"]))
            if end - start > 0.5:
                subclips.append(clip.subclip(start, end))

        if not subclips:
            st.error("AI 找不到符合的片段。")
            st.stop()

        final_clip = concatenate_videoclips(subclips, method="compose")

        # 輸出設定
        output_path = "ai_output_temp.mp4"
        logger = StreamlitLogger(progress_bar, status_text)

        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            logger=logger,
            preset="ultrafast",
        )

        status_box.update(label="✅ 所有步驟完成！", state="complete")

        # 7. 顯示結果與下載
        st.success(f"影片剪輯完成！總長度: {final_clip.duration:.1f} 秒")

        # 顯示預覽
        st.video(output_path)

        # 讀取檔案提供下載
        with open(output_path, "rb") as file:
            btn = st.download_button(
                label=f"📥 下載影片 ({final_filename})",
                data=file,
                file_name=final_filename,
                mime="video/mp4",
            )

        # 清理資源 (移至 finally 或在此做非關鍵清理)
        if video_file:
            try:
                genai.delete_file(video_file.name)
            except:
                pass

    except Exception as e:
        st.error(f"發生錯誤: {e}")

    finally:
        # 1. 優先關閉 MoviePy 資源，釋放檔案鎖定
        if clip:
            try:
                clip.close()
            except:
                pass
        if final_clip:
            try:
                final_clip.close()
            except:
                pass

        # 2. 嘗試刪除輸入暫存檔 (加入重試機制解決 Windows 權限錯誤)
        if os.path.exists(temp_video_path):
            try:
                os.remove(temp_video_path)
            except PermissionError:
                # 如果檔案被鎖住，等待 1 秒後重試，再失敗則忽略
                time.sleep(1)
                try:
                    os.remove(temp_video_path)
                except:
                    pass
            except Exception:
                pass

        # 3. 清理輸出暫存檔
        if os.path.exists("ai_output_temp.mp4"):
            try:
                os.remove("ai_output_temp.mp4")
            except PermissionError:
                time.sleep(1)
                try:
                    os.remove("ai_output_temp.mp4")
                except:
                    pass
            except:
                pass
