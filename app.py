from google import genai
from google.genai import types
import streamlit as st
import json
import os
import io
import time
import re
import urllib.parse
from datetime import datetime
from gtts import gTTS
from PIL import Image

# Fallback for Audio Speed-Up & PDF Generation
try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Gemma 4 Bio Shield - Africa", page_icon="🌾", layout="centered")

# --- INITIALIZE SESSION STATE ---
def init_session_state():
    defaults = {
        "selected_country": "",
        "selected_season": "",
        "uploaded_image": None,
        "analysis_status": None, # "not_a_plant", "unknown_plant", "needs_clarification", "diagnosed"
        "analysis_result": None,
        "clarification_answers": {},
        "audio_payloads": {},
        "uploader_key": 0,
        "history": []
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# --- HELPER FUNCTIONS ---
def compress_image(image):
    """Resizes and compresses image to save data and speed up AI response."""
    max_size = (800, 800)
    image.thumbnail(max_size, Image.Resampling.LANCZOS)
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG', optimize=True, quality=80)
    return img_byte_arr.getvalue()

def reset_diagnosis():
    """Clears ONLY image and diagnostic data while preserving Country & Season selection."""
    st.session_state.uploaded_image = None
    st.session_state.analysis_status = None
    st.session_state.analysis_result = None
    st.session_state.clarification_answers = {}
    st.session_state.audio_payloads = {}
    st.session_state.uploader_key += 1
    st.rerun()

def generate_pdf(result, date_str):
    """Generates a structured PDF report using FPDF with safe character encoding."""
    pdf = FPDF()
    pdf.add_page()
    
    def safe_text(txt):
        if not txt:
            return ""
        return str(txt).encode('latin-1', 'replace').decode('latin-1')

    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, txt=safe_text("Gemma 4 Bio Shield - Africa Report"), ln=True, align='C')
    
    pdf.set_font("Arial", 'I', 10)
    pdf.cell(200, 10, txt=safe_text(f"Date: {date_str} | Country: {st.session_state.selected_country}"), ln=True, align='C')
    
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(200, 10, txt=safe_text(f"Diagnosis: {result.get('pathology_name', 'Unknown')}"), ln=True)
    pdf.cell(200, 10, txt=safe_text(f"Accuracy: {result.get('confidence_percentage', 0)}% | Severity: {result.get('severity_level', 'Unknown')}"), ln=True)
    
    pdf.set_font("Arial", '', 11)
    pdf.multi_cell(0, 10, txt=safe_text(f"Symptoms: {result.get('symptom_confirmation', '')}"))
    
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(200, 10, txt=safe_text("Regional Treatment Plan:"), ln=True)
    pdf.set_font("Arial", '', 11)
    for step in result.get("local_treatment_plan", []):
        t_text = safe_text(f"- {step.get('treatment')} (Cost: {step.get('cost_level')} | Available: {step.get('availability')})")
        pdf.multi_cell(0, 10, txt=t_text)
        
    return pdf.output(dest='S').encode('latin-1')

# --- HEADER & HISTORY ---
st.title("🌾 Gemma 4 Bio Shield (Pan-Africa)")
st.caption("AI for Social Impact — Offline-Optimized & Multilingual Agricultural Diagnostic Platform")

with st.expander("📖 View Previous Diagnoses (Session History)"):
    if not st.session_state.history:
        st.info("No previous diagnoses recorded in this session yet.")
    else:
        for idx, record in enumerate(reversed(st.session_state.history)):
            st.markdown(f"**#{len(st.session_state.history) - idx} | {record['date']}**")
            st.write(f"🌍 **Country:** {record['country']} ({record['season']})")
            st.write(f"🩺 **Diagnosis:** {record['pathology']} (`{record['accuracy']}% Accuracy`)")
            st.write(f"💡 **Key Tip:** {record['guidance']}")
            st.markdown("---")

st.markdown("---")

# --- STEP 1: PAN-AFRICAN FIELD PARAMETERS ---
st.markdown("### 🌍 Step 1: Regional Field Parameters")
st.write("Select your country and current season to adapt AI diagnostics to local conditions.")

african_countries = [
    "", 
    "Algeria", 
    "Angola", 
    "Burkina Faso", 
    "Cameroon", 
    "Democratic Republic of the Congo (DRC)", 
    "Egypt", 
    "Ethiopia", 
    "Ghana", 
    "Ivory Coast (Côte d'Ivoire)", 
    "Kenya", 
    "Mali", 
    "Morocco", 
    "Mozambique", 
    "Nigeria", 
    "Rwanda", 
    "Senegal", 
    "South Africa", 
    "Sudan", 
    "Tanzania", 
    "Uganda", 
    "Zambia", 
    "Zimbabwe", 
    "Other African Country"
]

col1, col2 = st.columns(2)
with col1:
    country_val = st.selectbox(
        "Select African Country", 
        african_countries, 
        index=african_countries.index(st.session_state.selected_country) if st.session_state.selected_country in african_countries else 0
    )
with col2:
    seasons = ["", "Rainy / Wet Season", "Dry Season / Harmattan", "Inter-Monsoon / Planting Season"]
    season_val = st.selectbox(
        "Current Season", 
        seasons, 
        index=seasons.index(st.session_state.selected_season) if st.session_state.selected_season in seasons else 0
    )

st.session_state.selected_country = country_val
st.session_state.selected_season = season_val

if not (country_val and season_val):
    st.warning("🔒 Please select both your Country and Season above to unlock crop image uploading.")
    st.stop()

# --- STEP 2: DUAL CAPTURE MODES ---
st.markdown("---")
st.markdown("### 📸 Step 2: Crop Photo Input")

tab_upload, tab_camera = st.tabs(["📁 Upload Image File", "📸 Take Photo Directly"])
img_input = None

with tab_upload:
    uploaded_file = st.file_uploader(
        "Upload a clear photo of the affected leaf, stem, or fruit", 
        type=["jpg", "jpeg", "png"],
        key=f"file_uploader_{st.session_state.uploader_key}"
    )
    if uploaded_file:
        img_input = Image.open(uploaded_file)

with tab_camera:
    st.caption("💡 Tip: Ensure proper daylight and focus closely on symptoms.")
    camera_file = st.camera_input(
        "Take a photo of the affected plant",
        key=f"camera_input_{st.session_state.uploader_key}"
    )
    if camera_file:
        img_input = Image.open(camera_file)

if img_input:
    if img_input.mode in ("RGBA", "P"):
        img_input = img_input.convert("RGB")
    st.session_state.uploaded_image = img_input
    st.image(img_input, caption="Selected Specimen", use_container_width=True)

# --- AI ANALYSIS FUNCTION ---
def run_analysis():
    with st.spinner("🔄 Evaluating specimen with Gemma 4 across regional parameters..."):
        try:
            img = st.session_state.uploaded_image
            img_bytes = compress_image(img)
            
            contents_list = [types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg')]
            
            context_string = f"Country: {st.session_state.selected_country}, Season: {st.session_state.selected_season}."
            if st.session_state.clarification_answers:
                context_string += f" User structured answers: {json.dumps(st.session_state.clarification_answers)}"

            system_instruction = """
            You are an expert tropical and sub-tropical agricultural pathologist specialized in African agriculture.
            Always return your response STRICTLY as valid JSON matching the requested schema. Do not use Markdown block syntax.
            """

            prompt = f"""
            Analyze the attached image and metadata: {context_string}

            TASKS & RULES:
            1. VERIFY PLANT STATUS & USER INPUT:
               - If NOT a plant, set status to "not_a_plant". State what was detected in "message".
               - If user answers are gibberish, nonsensical, or completely unrelated to agriculture, set status to "needs_clarification" and put a polite message in "message" asking them to re-answer clearly.
               - If plant species is completely unclear, set status to "unknown_plant" and ask 1 question asking for the crop name.
               - If confidence < 80% or answers are incomplete, set status to "needs_clarification". Provide selectable options AND clear questions.
               - If confidence >= 80% and valid answers, set status to "diagnosed" and provide pathology and realistic local continental market treatments.

            2. AUDIO SUMMARIES REQUIRED (PUNCTUATION & PAUSE CONTROL):
               - Provide natural audio scripts inside "audio_summaries" for: English, Nigerian Pidgin, Hausa, French, Swahili, Arabic, and Portuguese.
               - PACING & PAUSES: Insert frequent ellipses (...) and full stops (.) throughout sentences to force the TTS engine to pause naturally.

            SCHEMA STRICT JSON:
            {{
                "status": "diagnosed" | "needs_clarification" | "unknown_plant" | "not_a_plant",
                "message": "Explanation note or question reason",
                "pathology_name": "Disease/Pest name or Unknown",
                "confidence_percentage": 85,
                "severity_level": "Low" | "Moderate" | "High",
                "symptom_confirmation": "Visual indicators observed",
                "clarification_questions": [
                    {{
                        "id": "q1",
                        "question": "Question text?",
                        "options": ["Option A", "Option B", "Option C"]
                    }}
                ],
                "local_treatment_plan": [
                    {{"treatment": "Remedy step", "cost_level": "Low-Cost", "availability": "Local regional market / Agro-dealer"}}
                ],
                "preventative_guidance": "Key tip to prevent spread",
                "audio_summaries": {{
                    "English": "Your crop shows signs of... [Disease name]... Please apply... [Exact Treatment]...",
                    "Nigerian Pidgin": "Pidgin audio text with... ellipses... and periods...",
                    "Hausa": "Hausa audio text with... ellipses... and periods...",
                    "French": "Votre culture présente des signes de... [Maladie]...",
                    "Swahili": "Mazao yako yanaonyesha dalili za... [Ugonjwa]...",
                    "Arabic": "محصولك تظهر عليه علامات... [اسم المرض]...",
                    "Portuguese": "A sua cultura apresenta sinais de... [Doença]..."
                }}
            }}
            """

            # Append audio payloads if recorded safely outside forms
            if "audio_payloads" in st.session_state and st.session_state.audio_payloads:
                for q_id, audio_bytes_val in st.session_state.audio_payloads.items():
                    if audio_bytes_val:
                        contents_list.append(types.Part.from_bytes(data=audio_bytes_val, mime_type='audio/wav'))
                        prompt += f" [Note: Voice note audio provided for question {q_id}]"

            contents_list.append(prompt)

            api_key = st.secrets.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                st.error("❌ GOOGLE_API_KEY is missing. Please add it to your Streamlit secrets.")
                return

            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemma-4-31b-it',
                contents=contents_list,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )

            raw_text = response.text
            clean_text = re.sub(r'^```json\s*|\s*```$', '', raw_text.strip(), flags=re.MULTILINE)
            result_data = json.loads(clean_text)
            
            st.session_state.analysis_status = result_data.get("status", "failed")
            st.session_state.analysis_result = result_data
            st.rerun()

        except Exception as e:
            st.error(f"❌ Analysis failed: {str(e)}")

if st.session_state.uploaded_image and st.session_state.analysis_status is None:
    if st.button("🔍 Send for Pan-African Diagnosis", type="primary", use_container_width=True):
        run_analysis()

# --- AUDIO PLAYER RENDERER (7 LANGUAGES SUPPORTED) ---
def render_audio_section(audio_summaries_dict):
    st.markdown("#### 🔊 Audio Diagnosis Summary")
    
    lang_mapping = {
        "English": {"lang": "en", "tld": "com"},
        "Nigerian Pidgin": {"lang": "en", "tld": "com.ng"},
        "Hausa": {"lang": "ha", "tld": "com"},
        "French": {"lang": "fr", "tld": "com"},
        "Swahili": {"lang": "sw", "tld": "com"},
        "Arabic": {"lang": "ar", "tld": "com"},
        "Portuguese": {"lang": "pt", "tld": "com"}
    }
    
    selected_lang = st.selectbox("Select Audio Language / Accent", list(lang_mapping.keys()), key="audio_lang_selector")
    text_to_speak = audio_summaries_dict.get(selected_lang, audio_summaries_dict.get("English", ""))
    
    if text_to_speak:
        st.write(f"🗣️ *{selected_lang}:* {text_to_speak}")
        config = lang_mapping[selected_lang]
        try:
            tts = gTTS(text=text_to_speak, lang=config["lang"], tld=config["tld"], slow=False)
            audio_buffer = io.BytesIO()
            tts.write_to_fp(audio_buffer)
            audio_buffer.seek(0)
            
            played_audio = False
            if HAS_PYDUB:
                try:
                    sound = AudioSegment.from_file(audio_buffer, format="mp3")
                    sped_up_sound = sound.speedup(playback_speed=1.3, chunk_size=150, crossfade=25)
                    output_buffer = io.BytesIO()
                    sped_up_sound.export(output_buffer, format="mp3")
                    output_buffer.seek(0)
                    st.audio(output_buffer.getvalue(), format="audio/mp3")
                    played_audio = True
                except Exception:
                    # Fall back safely if FFmpeg binary is missing on host environment
                    pass
            
            if not played_audio:
                st.audio(audio_buffer.getvalue(), format="audio/mp3")
                
        except Exception:
            st.info("💡 Audio playback temporarily unavailable offline.")

# --- RESULT HANDLERS ---
status = st.session_state.analysis_status
result = st.session_state.analysis_result

if status == "not_a_plant":
    st.error(f"🛑 **Analysis Halted:** {result.get('message', 'The image provided does not appear to be a plant.')}")
    if "audio_summaries" in result:
        render_audio_section(result["audio_summaries"])
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 Start New Diagnosis", type="primary", use_container_width=True):
        reset_diagnosis()

elif status in ["unknown_plant", "needs_clarification"]:
    st.warning(f"🟡 **Clarification Needed:** {result.get('message', 'Additional information required.')}")
    
    answers = {}
    audio_payloads = {}
    
    with st.form("clarification_form"):
        for q in result.get("clarification_questions", []):
            q_id = q.get("id")
            q_text = q.get("question")
            options = q.get("options", [])
            
            st.markdown(f"**{q_text}**")
            
            # Selectable Options
            selected_option = None
            if options:
                selected_option = st.radio(
                    "Select matching condition:", 
                    ["(Select option or use text below)"] + options, 
                    key=f"radio_{q_id}"
                )
            
            # Text Input inside form
            text_val = st.text_input("Or type custom description:", key=f"text_{q_id}")

            # Save form inputs
            if selected_option and selected_option != "(Select option or use text below)":
                answers[q_id] = selected_option
            elif text_val:
                answers[q_id] = text_val
            else:
                answers[q_id] = None
        
        submitted = st.form_submit_button("Submit Answers & Re-Analyze", type="primary", use_container_width=True)

    # Audio inputs placed OUTSIDE the form to avoid Streamlit state bugs/clearing issues
    st.markdown("#### 🎙️ Optional Voice Input")
    st.caption("If you prefer to speak your answers instead of typing, record your voice note below:")
    for q in result.get("clarification_questions", []):
        q_id = q.get("id")
        voice_val = st.audio_input(f"Record answer for: {q.get('question')}", key=f"voice_{q_id}")
        if voice_val:
            audio_payloads[q_id] = voice_val.getvalue()

    if submitted:
        # Merge answers and voice payloads
        final_answers = {}
        for q in result.get("clarification_questions", []):
            q_id = q.get("id")
            if answers.get(q_id):
                final_answers[q_id] = answers[q_id]
            elif q_id in audio_payloads and audio_payloads[q_id]:
                final_answers[q_id] = "[Voice Audio Note Provided]"
            else:
                final_answers[q_id] = "No answer provided"

        st.session_state.clarification_answers = final_answers
        st.session_state.audio_payloads = audio_payloads
        st.session_state.analysis_status = None
        st.rerun()

elif status == "diagnosed":
    st.markdown("---")
    st.markdown("### 🩺 Diagnostic Results")
    
    severity = result.get('severity_level', 'Unknown').lower()
    if severity == 'high':
        st.error("🚨 **HIGH ALERT:** Severe condition detected. Isolate affected crops immediately.")
    elif severity == 'moderate':
        st.warning("⚠️ **MODERATE:** Apply recommended regional treatments promptly.")
    else:
        st.success("✅ **LOW RISK:** Mild condition observed. Follow standard preventative actions.")

    acc = result.get('confidence_percentage', 0)
    st.markdown(f"**Accuracy Confidence:** `{acc}%`")
    st.markdown(f"**Pathology / Disease:** `{result.get('pathology_name', 'Unknown')}`")
    st.markdown(f"**Symptoms Observed:** {result.get('symptom_confirmation', '')}")
    
    st.markdown("#### 💊 Regional Treatment Plan")
    for step in result.get("local_treatment_plan", []):
        st.markdown(f"* **{step.get('treatment')}**  \n  *Cost:* `{step.get('cost_level')}` | *Availability:* {step.get('availability')}")
        
    st.markdown(f"**🛡️ Preventative Guidance:** {result.get('preventative_guidance', '')}")

    history_entry = {
        "date": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "country": st.session_state.selected_country,
        "season": st.session_state.selected_season,
        "pathology": result.get('pathology_name', 'Unknown'),
        "accuracy": acc,
        "guidance": result.get('preventative_guidance', 'N/A')
    }
    if not any(h['date'] == history_entry['date'] for h in st.session_state.history):
        st.session_state.history.append(history_entry)

    st.markdown("---")
    if "audio_summaries" in result:
        render_audio_section(result["audio_summaries"])

    st.markdown("---")
    st.markdown("### 📤 Export & Share Tools")
    
    col_exp1, col_exp2, col_exp3 = st.columns(3)
    
    with col_exp1:
        if HAS_FPDF:
            pdf_bytes = generate_pdf(result, datetime.now().strftime("%Y-%m-%d"))
            st.download_button("📥 Download PDF", data=pdf_bytes, file_name="crop_diagnosis.pdf", mime="application/pdf", use_container_width=True)
        else:
            report_text = f"Diagnosis: {result.get('pathology_name')}\nAccuracy: {acc}%\nCountry: {st.session_state.selected_country}"
            st.download_button("📥 Download Summary", data=report_text, file_name="crop_diagnosis.txt", mime="text/plain", use_container_width=True)

    with col_exp2:
        img_byte_arr = io.BytesIO()
        st.session_state.uploaded_image.save(img_byte_arr, format='JPEG')
        st.download_button("🖼️ Download Image", data=img_byte_arr.getvalue(), file_name="crop_specimen.jpg", mime="image/jpeg", use_container_width=True)

    with col_exp3:
        wa_text = f"🌾 *Gemma 4 Bio Shield*\n🌍 *Country:* {st.session_state.selected_country}\n🩺 *Disease:* {result.get('pathology_name')} ({acc}% Accuracy)\n⚠️ *Severity:* {severity.capitalize()}\n💡 *Treatment:* {result.get('preventative_guidance')}"
        encoded_wa_text = urllib.parse.quote(wa_text)
        st.link_button("📲 Share WhatsApp", f"https://wa.me/?text={encoded_wa_text}", use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)
    
    if st.button("🔄 Start New Diagnosis", type="primary", use_container_width=True):
        reset_diagnosis()

    st.markdown("---")
    st.caption("*Disclaimer: AI-generated advisory tool. Consult local agricultural extension officers for widespread regional outbreaks.*")