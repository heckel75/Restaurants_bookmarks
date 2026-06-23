from website_text import fetch_website_text


url = "https://www.alfis-paris.com/"
text = fetch_website_text(
    url,
    target_name="Alfi's",
    target_address="26 Rue du Mont Thabor, 75001 Paris, France",
)

print("Characters fetched:", len(text))
print(text[:1500])