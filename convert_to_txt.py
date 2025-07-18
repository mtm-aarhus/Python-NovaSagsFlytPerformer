import pandas as pd

# Load your Excel file
excel_path = "input_cases.xlsx"   # Make sure this file exists
txt_output_path = "input_cases.txt"

# Read the Excel file (must have the exact column names)
df = pd.read_excel(excel_path)

# Check required columns exist
required_columns = {"sagsnummer", "oldazident", "newazident"}
if not required_columns.issubset(df.columns.str.lower()):
    raise ValueError("Excel file must contain columns: sagsnummer, oldazident, newazident")

# Normalize column names to lowercase for safety
df.columns = [col.lower() for col in df.columns]

# Write each row as a line in the txt file
with open(txt_output_path, "w", encoding="utf-8") as f:
    for _, row in df.iterrows():
        sagsnummer = str(row["sagsnummer"]).strip()
        oldazident = str(row["oldazident"]).strip()
        newazident = str(row["newazident"]).strip()
        if sagsnummer and oldazident and newazident:
            f.write(f"{sagsnummer},{oldazident},{newazident}\n")

print(f"✅ Created {txt_output_path} with {len(df)} rows.")
