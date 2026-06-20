import pytest
import json
from unittest.mock import patch, MagicMock
from app.core.llm_client import (
    llm_client, 
    GeminiRateLimitError, 
    GeminiServerError, 
    CircuitOpenError,
    AllProvidersFailedError,
    _record_circuit_failure,
    _record_circuit_success,
    CircuitState
)

def test_llm_client_success(mock_llm_response):
    with patch("app.core.llm_client._call_gemini") as mock_gemini:
        mock_gemini.return_value = (json.dumps(mock_llm_response), 10, 20)
        
        result, p, c = llm_client.complete_json("prompt")
        
        assert result == mock_llm_response
        assert p == 10
        assert c == 20
        mock_gemini.assert_called_once()

def test_llm_client_fallback_on_429(mock_llm_response):
    with patch("app.core.llm_client._call_gemini") as mock_gemini:
        with patch("app.core.llm_client._call_openrouter") as mock_or:
            # Gemini raises rate limit
            mock_gemini.side_effect = GeminiRateLimitError("429 Quota Exceeded")
            
            # OpenRouter succeeds
            mock_or.return_value = (json.dumps(mock_llm_response), 5, 5)
            
            result, p, c = llm_client.complete_json("prompt")
            
            assert result == mock_llm_response
            assert p == 5
            assert c == 5
            mock_or.assert_called_once()

def test_llm_client_all_failed():
    with patch("app.core.llm_client._call_gemini") as mock_gemini:
        with patch("app.core.llm_client._call_openrouter") as mock_or:
            mock_gemini.side_effect = GeminiServerError("503 Backend Error")
            mock_or.side_effect = Exception("OpenRouter also down")
            
            with pytest.raises(AllProvidersFailedError):
                llm_client.complete_json("prompt")

def test_llm_client_retry_prompt():
    with patch("app.core.llm_client._call_gemini") as mock_gemini:
        # First call returns invalid JSON
        # Second call returns valid JSON
        mock_gemini.side_effect = [
            ("invalid json syntax", 10, 10),
            ('{"assigned_category": "Food"}', 15, 15)
        ]
        
        result, p, c, used_v2 = llm_client.complete_json_with_retry_prompt("v1", "v2")
        
        assert result == {"assigned_category": "Food"}
        assert used_v2 is True
        assert mock_gemini.call_count == 2
