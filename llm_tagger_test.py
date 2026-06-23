from llm_tagger import tag_restaurant, result_to_sheet_values


test_row = {
    "Name": "Gloria Osteria Paris",
    "Address": "41 Rue de Lille, 75007 Paris, France",
    "City": "Paris",
    "Postal Code": "75007",
    "Arrondissement": "7",
    "Town": "",
    "Website": "https://gloria-osteria.com/fr/gloria-osteria-paris",
    "Instagram": "",
    "Facebook": "",
    "Notes": "Italian trattoria. Pasta, pizza, lively decor, social dining. No explicit delivery or takeaway evidence.",
    "Cuisine": "",
    "Vibe": "",
    "Features": "",
    "Delivery": "UNKNOWN",
    "Takeaway": "UNKNOWN",
}

result = tag_restaurant(test_row)
sheet_values = result_to_sheet_values(result)

print("Raw structured result:")
print(result.model_dump_json(indent=2))

print("\nValues ready for Google Sheets:")
for key, value in sheet_values.items():
    print(f"{key}: {value}")

    