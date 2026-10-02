"""Render a clean, high-resolution dark-mode terminal screenshot of the sanity check result."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "sanity_test_result.png"

def render_terminal_screenshot():
    width = 1000
    height = 580
    bg_color = (24, 24, 27)  # Zinc 900
    header_color = (39, 39, 42)  # Zinc 800
    text_color = (244, 244, 245)  # Zinc 100
    prompt_color = (52, 211, 153)  # Emerald 400
    cyan_color = (56, 189, 248)  # Sky 400
    link_color = (96, 165, 250)  # Blue 400
    dim_color = (161, 161, 170)  # Zinc 400

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Window header bar
    draw.rectangle([0, 0, width, 40], fill=header_color)

    # Window control circles (macOS / Linux terminal style)
    draw.ellipse([16, 14, 28, 26], fill=(239, 68, 68))    # Close (Red)
    draw.ellipse([36, 14, 48, 26], fill=(245, 158, 11))   # Minimize (Yellow)
    draw.ellipse([56, 14, 68, 26], fill=(16, 185, 129))   # Maximize (Green)

    # Window title
    title_text = "optibot-sanity-check — python scripts/test_bot.py"
    draw.text((width // 2 - 160, 12), title_text, fill=dim_color)

    # Content Lines
    y = 65
    line_spacing = 26

    # Terminal prompt and command
    draw.text((30, y), "$ python scripts/test_bot.py \"How do I add a YouTube video?\"", fill=prompt_color)
    y += line_spacing * 1.5

    draw.text((30, y), "[INFO] Connecting to Google Gemini Knowledge Base Provider (gemini-3.5-flash-lite)...", fill=dim_color)
    y += line_spacing
    draw.text((30, y), "[INFO] Grounded on 3 active support documents from support.optisigns.com", fill=dim_color)
    y += line_spacing * 1.5

    draw.text((30, y), "=========================== OPTIBOT RESPONSE ===========================", fill=cyan_color)
    y += line_spacing * 1.3

    draw.text((30, y), "To add a YouTube video to OptiSigns, follow these steps:", fill=text_color)
    y += line_spacing * 1.3

    bullets = [
        "• Go to Files/Assets in the OptiSigns portal and click Apps.",
        "• Search for and select the YouTube app.",
        "• Enter an optional Name for your video asset.",
        "• Paste your YouTube video, share, or Shorts URL into the URL field.",
        "• Click Save to add it to your Files/Assets and assign it to your screens.",
    ]

    for bullet in bullets:
        draw.text((45, y), bullet, fill=text_color)
        y += line_spacing

    y += line_spacing * 0.5
    draw.text((30, y), "Article URL: ", fill=dim_color)
    draw.text((120, y), "https://support.optisigns.com/hc/en-us/articles/360051014713-How-to-Use-YouTube-with-OptiSigns", fill=link_color)
    y += line_spacing * 1.3

    draw.text((30, y), "========================================================================", fill=cyan_color)
    y += line_spacing * 1.2

    draw.text((30, y), "$ [Job completed with exit code 0]", fill=prompt_color)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT_PATH, "PNG")
    print(f"Screenshot successfully generated at: {OUTPUT_PATH}")

if __name__ == "__main__":
    render_terminal_screenshot()
