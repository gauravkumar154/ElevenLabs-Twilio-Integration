import os
import json
import base64
import asyncio
import logging
import audioop
from aiohttp import web, ClientSession, WSMsgType
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

async def get_signed_url():
    url = f"https://api.elevenlabs.io/v1/convai/conversation/get_signed_url?agent_id={os.getenv('ELEVENLABS_AGENT_ID')}"
    headers = {"xi-api-key": os.getenv("ELEVENLABS_API_KEY")}
    
    async with ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                error = await resp.text()
                logging.error(f"Failed to get signed URL: {error}")
                raise Exception(f"ElevenLabs API error: {resp.status}")
            return (await resp.json()).get("signed_url")

async def twilio_webhook(request):
    try:
        return web.Response(
            content_type='text/xml',
            text=f"""<?xml version='1.0' encoding='UTF-8'?>
            <Response>
                <Connect>
                    <Stream url='wss://{request.host}/media-stream'>
                        <Parameter name="format" value="mulaw"/>
                    </Stream>
                </Connect>
            </Response>"""
        )
    except Exception as e:
        logging.error(f"Webhook error: {str(e)}")
        return web.Response(status=500)

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    logging.info("Twilio media stream connected")

    stream_sid = None
    elevenlabs_ws = None
    session = None

    try:
        signed_url = await get_signed_url()
        session = ClientSession()
        elevenlabs_ws = await session.ws_connect(signed_url)
        logging.info("Connected to ElevenLabs")

        async def handle_twilio():
            nonlocal stream_sid
            try:
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        event_type = data.get("event")
                        
                        if event_type == "start":
                            stream_sid = data["start"]["streamSid"]
                            logging.info(f"Stream started: {stream_sid}")
                            
                        elif event_type == "media":
                            # Don't modify the audio data, just pass it through like Node.js does
                            audio_message = {
                                "user_audio_chunk": data["media"]["payload"]  # Already base64
                            }
                            await elevenlabs_ws.send_json(audio_message)
                            logging.info("Forwarded audio chunk to ElevenLabs")
                            
                        elif event_type == "stop":
                            logging.info("Stream stopped")
                            await elevenlabs_ws.close()
                            return
                            
            except Exception as e:
                logging.error(f"Twilio handler error: {str(e)}")
                raise

        async def handle_elevenlabs():
            try:
                async for msg in elevenlabs_ws:
                    if msg.type == WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            message_type = data.get("type")
                            
                            if message_type == "conversation_initiation_metadata":
                                logging.info("Received conversation initiation metadata")
                                
                            elif message_type == "audio" and stream_sid:
                                # Match Node.js structure exactly
                                if data.get("audio_event", {}).get("audio_base_64"):
                                    await ws.send_json({
                                        "event": "media",
                                        "streamSid": stream_sid,
                                        "media": {
                                            "payload": data["audio_event"]["audio_base_64"]
                                        }
                                    })
                                    logging.info("Forwarded audio response to Twilio")
                                    
                            elif message_type == "interruption":
                                await ws.send_json({
                                    "event": "clear",
                                    "streamSid": stream_sid
                                })
                                
                            elif message_type == "ping":
                                if data.get("ping_event", {}).get("event_id"):
                                    pong_response = {
                                        "type": "pong",
                                        "event_id": data["ping_event"]["event_id"]
                                    }
                                    await elevenlabs_ws.send_json(pong_response)
                            
                            elif message_type == "agent_response":
                                agent_response = data.get("agent_response_event", {}).get("agent_response")
                                if agent_response:
                                    logging.info(f"Agent: {agent_response}")
                                    
                        except Exception as e:
                            logging.error(f"Error processing ElevenLabs message: {str(e)}")
                            
            except Exception as e:
                logging.error(f"ElevenLabs handler error: {str(e)}")
                raise

        # Create and start tasks
        twilio_task = asyncio.create_task(handle_twilio())
        elevenlabs_task = asyncio.create_task(handle_elevenlabs())
        
        # Wait for both tasks
        await asyncio.gather(twilio_task, elevenlabs_task)

    except Exception as e:
        logging.error(f"Main error: {str(e)}")
    finally:
        if elevenlabs_ws and not elevenlabs_ws.closed:
            await elevenlabs_ws.close()
        if session and not session.closed:
            await session.close()
        if not ws.closed:
            await ws.close()
        logging.info("Connections closed")

    return ws

app = web.Application()
app.router.add_post("/incoming-call-eleven", twilio_webhook)
app.router.add_get("/media-stream", websocket_handler)
app.router.add_get("/", lambda r: web.json_response({"status": "ok"}))

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)