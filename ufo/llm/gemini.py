import functools
import base64
import re
import time
import random
from typing import Any, Dict, List, Optional

import google.generativeai as genai

from ufo.llm.base import BaseService
from ufo.utils import print_with_color


class GeminiService(BaseService):
    """
    A service class for Gemini models.
    """

    def __init__(self, config: Dict[str, Any], agent_type: str):
        """
        Initialize the Gemini service.
        :param config: The configuration.
        :param agent_type: The agent type.
        """
        self.config_llm = config[agent_type]
        self.config = config
        self.model_name = self.config_llm["API_MODEL"]
        self.prices = self.config["PRICES"]
        self.max_retry = self.config["MAX_RETRY"]
        self.api_type = self.config_llm["API_TYPE"].lower()
        
        # Initialize the Gemini API
        genai.configure(api_key=self.config_llm["API_KEY"])
        self.model = genai.GenerativeModel(self.model_name)

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        n: int = 1,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Generates completions for a given list of messages.
        :param messages: The list of messages to generate completions for.
        :param n: The number of completions to generate for each message.
        :param temperature: Controls the randomness of the generated completions.
        :param max_tokens: The maximum number of tokens in the generated completions.
        :param top_p: Controls the diversity of the generated completions.
        :param kwargs: Additional keyword arguments.
        :return: A list of generated completions and the estimated cost.
        """
        try:
            # Initialize response and cost
            response = None
            cost = 0.0

            # Convert messages to Gemini format
            gemini_messages = self.process_messages(messages)
            
            # Set generation config
            genai_config = {
                "max_output_tokens": max_tokens if max_tokens is not None else self.config["MAX_TOKENS"],
                "temperature": temperature if temperature is not None else self.config["TEMPERATURE"],
                "top_p": top_p if top_p is not None else self.config["TOP_P"],
            }
            
            # Generate response
            response = self.model.generate_content(
                contents=gemini_messages,
                generation_config=genai_config
            )
            
            # Calculate cost if enabled
            if self.config.get("ENABLE_COST_TRACKING", False):
                model_name = self.model_name
                model_costs = self.config.get("MODEL_COSTS", {})
                input_cost = model_costs.get(model_name, {}).get("input_cost", 0.0)
                output_cost = model_costs.get(model_name, {}).get("output_cost", 0.0)
                
                # Calculate input tokens (rough estimate)
                input_tokens = sum(len(msg.get("content", "").split()) for msg in messages)
                # Calculate output tokens (rough estimate)
                output_tokens = len(response.text.split())
                
                cost = (input_tokens * input_cost) + (output_tokens * output_cost)
            
            return self.get_text_from_all_candidates(response), cost
            
        except Exception as e:
            print(f"Error in Gemini chat completion: {str(e)}")
            raise e

    def process_messages(self, messages: List[Dict[str, str]]) -> List[Any]:
        """
        Process the given messages and extract prompts from them.
        :param messages: The messages to process.
        :return: A list of prompts extracted from the messages.
        """
        prompt_contents = []

        if isinstance(messages, dict):
            messages = [messages]
        for message in messages:
            if message["role"] == "system":
                prompt = f"Your general instruction: {message['content']}"
                prompt_contents.append(prompt)
            else:
                for content in message["content"]:
                    if content["type"] == "text":
                        prompt = content["text"]
                        prompt_contents.append(prompt)
                    elif content["type"] == "image_url":
                        prompt = self.base64_to_blob(content["image_url"]["url"])
                        prompt_contents.append({
                            "mime_type": prompt["mime_type"],
                            "data": prompt["data"]
                        })
        return prompt_contents

    def base64_to_blob(self, base64_str: str) -> Dict[str, str]:
        """
        Converts a base64 encoded image string to MIME type and binary data.
        :param base64_str: The base64 encoded image string.
        :return: A dictionary containing the MIME type and binary data.
        """
        match = re.match(r'data:(?P<mime_type>image/.+?);base64,(?P<base64_string>.+)', base64_str)

        if match:
            mime_type = match.group('mime_type')
            base64_string = match.group('base64_string')
        else:
            print("Error: Could not parse the data URL.")
            raise ValueError("Invalid data URL format.")

        return {
            "mime_type": mime_type,
            "data": base64.b64decode(base64_string)
        }

    def get_text_from_all_candidates(self, response: Any) -> List[Optional[str]]:
        """
        Extracts the concatenated text content from each candidate in the response.
        :param response: The response object from the Gemini API call.
        :return: A list where each element is the concatenated text from a candidate,
                or None if a candidate has no text parts.
        """
        all_texts = []
        if not response or not hasattr(response, 'candidates'):
            print("Warning: Response object does not contain candidates.")
            return all_texts

        for i, candidate in enumerate(response.candidates):
            candidate_text: str = ''
            any_text_part_found: bool = False
            non_text_parts_found: List[str] = []

            if not candidate or not hasattr(candidate, 'content') or not hasattr(candidate.content, 'parts'):
                print(f"Warning: Candidate {i} has no content or parts.")
                all_texts.append(None)
                continue

            for part in candidate.content.parts:
                # Check for non-text parts
                for field_name, field_value in part.__dict__.items():
                    if field_value is not None and field_name not in ('text', 'thought'):
                        if field_name not in non_text_parts_found:
                            non_text_parts_found.append(field_name)

                # Check if the part has text
                if hasattr(part, 'text') and isinstance(part.text, str):
                    # Skip parts marked as internal 'thought' if the attribute exists
                    if hasattr(part, 'thought') and part.thought:
                        continue
                    any_text_part_found = True
                    candidate_text += part.text

            if non_text_parts_found:
                print(
                    f'Warning: Candidate {i}: Contains non-text parts: {non_text_parts_found}. '
                    'Returning concatenated text from text parts only for this candidate.'
                )

            all_texts.append(candidate_text if any_text_part_found else None)

        return all_texts
