import google.generativeai as genai
import sys

GEMINI_API_KEY = "AIzaSyDRzgDArDdSN8Wj40ep3E0P0ZgwFp2YrBo"
genai.configure(api_key=GEMINI_API_KEY)
try:
    gemini_model = genai.GenerativeModel('gemini-2.5-flash')
    response = gemini_model.generate_content("테스트: 안녕하세요! 감정단어|수치 형식으로 아무거나 응답해주세요.")
    print("API 응답 성공:", response.text)
except Exception as e:
    print("API 오류 발생:", e)
