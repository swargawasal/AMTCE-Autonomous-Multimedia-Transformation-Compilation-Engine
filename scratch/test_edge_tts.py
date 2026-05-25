import asyncio
import edge_tts
import os

async def test():
    text = "Hello world, testing edge tts."
    voice = "en-US-ChristopherNeural"
    output = "test_audio.mp3"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output)
    if os.path.exists(output):
        print(f"Success! {os.path.getsize(output)} bytes")
    else:
        print("Failed to generate file.")

if __name__ == "__main__":
    asyncio.run(test())
