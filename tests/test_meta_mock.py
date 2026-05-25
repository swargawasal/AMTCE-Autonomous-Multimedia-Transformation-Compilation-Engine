import asyncio
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from Uploader_Modules.meta_uploader import AsyncMetaUploader

class TestMetaUploader(unittest.TestCase):
    
    def setUp(self):
        # Mock Env
        self.patcher = patch.dict(os.environ, {
            "ENABLE_META_UPLOAD": "yes",
            "META_PAGE_ID": "123",
            "META_PAGE_TOKEN": "token",
            "IG_BUSINESS_ID": "456",
            "IG_BUSINESS_TOKEN": "token",
            "META_UPLOAD_TYPE": "Reels",
            "SEND_TO_FACEBOOK": "on"
        })
        self.patcher.start()
        
        # Create dummy video file
        with open("test_video.mp4", "wb") as f:
            f.write(b"dummy content")
            
    def tearDown(self):
        self.patcher.stop()
        if os.path.exists("test_video.mp4"):
            os.remove("test_video.mp4")

    @patch('Uploader_Modules.meta_uploader.httpx.AsyncClient')
    def test_upload_success(self, mock_client_cls):
        # Setup Mock Client
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        
        # Helper to create async response
        def create_resp(data, status=200):
            mock = MagicMock()
            mock.status_code = status
            mock.json.return_value = data
            return mock

        # Mock Responses for sequential calls
        # 1. IG Init (POST)
        # 2. IG Upload (POST)
        # 3. IG Status (GET)
        # 4. IG Publish (POST)
        # 5. FB Init (POST)
        # 6. FB Upload (POST)
        # 7. FB Finish (POST)
        
        # Note: side_effect on async methods must return Awaitables?
        # Actually AsyncMock handles this.
        mock_client.post = AsyncMock()
        mock_client.get = AsyncMock()
        
        # IG Flow
        post_responses = [
            create_resp({"uri": "http://ig-up", "id": "ig_cont_1"}), # IG Init
            create_resp({"success": True}), # IG Upload
            create_resp({"id": "ig_media_1"}), # IG Publish
            
            create_resp({"video_id": "fb_vid_1", "upload_session_id": "fb_sess_1"}), # FB Init
            create_resp({"success": True}), # FB Upload
            create_resp({"video_id": "fb_vid_1"}) # FB Finish
        ]
        mock_client.post.side_effect = post_responses
        
        # IG Status Check
        mock_client.get.return_value = create_resp({"status_code": "FINISHED"})
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(AsyncMetaUploader.upload_to_meta("test_video.mp4", "caption"))
        finally:
            loop.close()
        
        print(f"Results: {results}")
        self.assertEqual(results['instagram']['status'], 'success')
        self.assertEqual(results['facebook']['status'], 'success')

    @patch('Uploader_Modules.meta_uploader.httpx.AsyncClient')
    def test_upload_failure_retry(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        
        mock_client.post = AsyncMock()
        mock_client.get = AsyncMock()
        
        def create_resp(data, status=200):
            mock = MagicMock()
            mock.status_code = status
            mock.json.return_value = data
            return mock

        # Simulation: First call 500, then Success
        post_responses = [
            create_resp({"error": "fail"}, status=500), # Fail 1
            create_resp({"uri": "http://ig-up", "id": "ig_cont_1"}), # Retry Success
            
            create_resp({"success": True}), # IG Upload
            create_resp({"id": "ig_media_1"}), # IG Publish
            
            create_resp({"video_id": "fb_vid_1", "upload_session_id": "fb_sess_1"}), # FB Init
            create_resp({"success": True}), # FB Upload
            create_resp({"video_id": "fb_vid_1"}) # FB Finish
        ]
        mock_client.post.side_effect = post_responses
        mock_client.get.return_value = create_resp({"status_code": "FINISHED"})
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(AsyncMetaUploader.upload_to_meta("test_video.mp4", "caption"))
        finally:
            loop.close()
            
        print(f"Retry Results: {results}")
        self.assertEqual(results['instagram']['status'], 'success')

if __name__ == '__main__':
    unittest.main()
