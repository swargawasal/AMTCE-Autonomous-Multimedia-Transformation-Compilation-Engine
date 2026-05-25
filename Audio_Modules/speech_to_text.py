import os
import logging
from typing import Dict, List, Optional
import time

logger = logging.getLogger("speech_to_text")

def transcribe_audio(audio_path: str, model_size: str = "base", compute_type: str = "int8", initial_prompt: Optional[str] = None) -> Optional[Dict]:
    """
    Transcribes audio using faster-whisper with local CPU-optimized execution.
    Features LAZY LOADING to ensure it never breaks the main pipeline if not installed.
    """
    if not os.path.exists(audio_path):
        logger.error(f"❌ Audio file not found for transcription: {audio_path}")
        return None

    try:
        # LAZY LOAD: Only import when explicitly called
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("❌ faster-whisper is not installed. Run: pip install faster-whisper")
        return None

    logger.info(f"🎙️ Initializing WhisperModel ({model_size}) on CPU with {compute_type}...")
    start_time = time.time()
    
    try:
        # Load model on CPU with INT8 quantization for safety and efficiency
        model = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        
        logger.info(f"🎙️ Transcribing {audio_path}...")
        segments, info = model.transcribe(audio_path, word_timestamps=True, initial_prompt=initial_prompt)
        
        transcript = ""
        word_level_data = []
        
        for segment in segments:
            transcript += segment.text + " "
            if hasattr(segment, 'words') and segment.words:
                for word in segment.words:
                    word_level_data.append({
                        "word": word.word,
                        "start": word.start,
                        "end": word.end,
                        "probability": word.probability
                    })
        
        duration = time.time() - start_time
        logger.info(f"✅ Transcription complete in {duration:.2f}s. Detected language: {info.language}")
        
        return {
            "transcript": transcript.strip(),
            "words": word_level_data,
            "language": info.language
        }

    except Exception as e:
        logger.error(f"❌ Transcription failed: {e}")
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    if len(sys.argv) > 1:
        res = transcribe_audio(sys.argv[1])
        if res:
            print("Transcript:", res["transcript"][:100], "...")
            print(f"Total words: {len(res['words'])}")
    else:
        print("Usage: python speech_to_text.py <audio_file>")
