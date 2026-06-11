from unittest.mock import patch
from connectors.deepseek import call_deepseek
from connectors.mistral import call_mistral
from connectors.gemini import call_gemini
from connectors.qwen_hf import call_qwen
import os

@patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test_key"})
@patch('requests.post')
def test_deepseek_connector(mock_post):
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "Test deepseek response", "reasoning_content": "reasoning"}}],
        "usage": {"total_tokens": 10}
    }
    result = call_deepseek("Hello")
    assert result["answer"] == "Test deepseek response"
    assert result["tokens_used"] == 10

@patch.dict(os.environ, {"MISTRAL_API_KEY": "test_key"})
@patch('connectors.mistral.Mistral')
def test_mistral_connector(mock_mistral):
    # Mocking the client structure
    mock_client = mock_mistral.return_value
    mock_client.chat.complete.return_value.choices = [
        type('obj', (object,), {'message': type('obj', (object,), {'content': 'Test mistral response'})})
    ]
    mock_client.chat.complete.return_value.usage = type('obj', (object,), {'total_tokens': 15})

    result = call_mistral("Hello")
    assert result["answer"] == "Test mistral response"
    assert result["tokens_used"] == 15

@patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
@patch('google.genai.Client')
def test_gemini_connector(mock_genai):
    mock_client = mock_genai.return_value
    mock_client.models.generate_content.return_value.text = "Test gemini response"
    
    result = call_gemini("Hello")
    assert result["answer"] == "Test gemini response"
    assert result["tokens_used"] == 0

@patch.dict(os.environ, {"HF_TOKEN": "test_key"})
@patch('connectors.qwen_hf.InferenceClient')
def test_qwen_connector(mock_hf):
    mock_client = mock_hf.return_value
    mock_client.chat.completions.create.return_value.choices = [
        type('obj', (object,), {'message': type('obj', (object,), {'content': 'Test qwen response'})})
    ]
    mock_client.chat.completions.create.return_value.usage = type('obj', (object,), {'total_tokens': 20})

    result = call_qwen("Hello")
    assert result["answer"] == "Test qwen response"
    assert result["tokens_used"] == 20
