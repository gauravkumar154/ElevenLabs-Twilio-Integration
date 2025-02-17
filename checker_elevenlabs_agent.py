import asyncio
import json
import base64
import audioop
import sounddevice as sd
import numpy as np
from aiohttp import ClientSession, WSMsgType

async def play_audio(audio_data, sample_rate=8000):
    """Play μ-law audio using sounddevice"""
    try:
        pcm_data = audioop.ulaw2lin(audio_data, 2)
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        sd.play(audio_array, samplerate=sample_rate)
        sd.wait()
    except Exception as e:
        print(f"Audio playback error: {str(e)}")

async def test_elevenlabs():
    ELEVENLABS_API_KEY = ""
    ELEVENLABS_AGENT_ID = ""
    
    async with ClientSession() as session:
        try:
            # Get signed URL
            url = f"https://api.elevenlabs.io/v1/convai/conversation/get_signed_url?agent_id={ELEVENLABS_AGENT_ID}"
            headers = {"xi-api-key": ELEVENLABS_API_KEY}
            
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    print("Failed to get signed URL:", await response.text())
                    return
                signed_url = (await response.json()).get("signed_url")

            async with session.ws_connect(signed_url) as ws:
                silence = b"\x00" * 320 
                await ws.send_json({
                    "user_audio_chunk": base64.b64encode(silence).decode(),
                    "text": ""
                })

                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        
                        if data.get("type") == "conversation_initiation_metadata":
                            meta = data.get("conversation_initiation_metadata_event", {})
                            audio_format = meta.get("agent_output_audio_format", "ulaw_8000")
                            sample_rate = int(audio_format.split("_")[-1])
                            print(f"Audio format: {audio_format}")

                        elif data.get("type") == "audio":
                            if audio_b64 := data.get("audio_event", {}).get("audio_base_64"):
                                audio_data = base64.b64decode(audio_b64)
                                print("Playing response...")
                                await play_audio(audio_data, sample_rate)

                        elif data.get("type") == "agent_response":
                            print("Agent:", data.get("agent_response_event", {}).get("agent_response"))

                    elif msg.type == WSMsgType.CLOSED:
                        break

        except Exception as e:
            print("Error:", str(e))

# Run with:
# pip install sounddevice numpy
# python3 check_eleven.py
asyncio.run(test_elevenlabs())