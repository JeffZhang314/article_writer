from google import genai

client = genai.Client(api_key="AIzaSyBWnSRNLpXJR6NvcTwb1DPoDayg2qhPNY4")

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="hello"
)

print(response.text)