import os
import re
import json
import argparse

def parse_transcript_to_turns(raw_text):
    lines = raw_text.split('\n')
    turns =[]
    current_turn = None

    # FORMAT 1: Matches lines with a colon (e.g., "Date of Encounter: 10/10/20")
    speaker_colon_regex = re.compile(r'^([A-Z][A-Za-z0-9\.\s]+):(.*)$')

    # FORMAT 2: Matches all-caps speaker names without colons (e.g., "DOCTOR Q131", "PATIENT?")
    speaker_nocolon_regex = re.compile(r'^([A-Z][A-Z0-9\s\?]+)$')

    for line in lines:
        clean = line.strip()
        if not clean:
            continue

        is_speaker = False
        speaker_name = ""
        spoken_text = ""

        match_colon = speaker_colon_regex.match(clean)
        if match_colon and len(match_colon.group(1)) < 50:
            is_speaker = True
            speaker_name = match_colon.group(1).strip()
            spoken_text = match_colon.group(2).strip()
        else:
            match_nocolon = speaker_nocolon_regex.match(clean)
            if match_nocolon and len(clean) < 40:
                is_speaker = True
                speaker_name = match_nocolon.group(1).strip()
                spoken_text = ""

        if is_speaker:
            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "index": len(turns),
                "speaker_line": speaker_name,
                "text_lines":[spoken_text] if spoken_text else[]
            }
        else:
            if current_turn:
                current_turn["text_lines"].append(clean)
            else:
                current_turn = {"index": 0, "speaker_line": "METADATA", "text_lines": [clean]}

    if current_turn:
        turns.append(current_turn)
    return turns

def get_segment_text(turns, start_idx, end_idx):
    output = ""
    start_idx = max(0, start_idx)
    end_idx = min(len(turns) - 1, end_idx)
    for i in range(start_idx, end_idx + 1):
        t = turns[i]
        output += f"[TURN {t['index']}] {t['speaker_line']}: {' '.join(t['text_lines'])}\n"
    return output

def main():
    parser = argparse.ArgumentParser(description="Parse raw medical transcript .txt files into JSON turns.")
    parser.add_argument("-i", "--input_dir", required=True, help="Directory containing input .txt files")
    parser.add_argument("-o", "--output_dir", required=True, help="Directory to save output files")
    
    args = parser.parse_args()

    # Ensure output directory exists
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)
        print(f"Created output directory: {args.output_dir}")

    # Get all txt files
    all_files =[f for f in os.listdir(args.input_dir) if f.lower().endswith('.txt')]
    
    if not all_files:
        print(f"No .txt files found in {args.input_dir}.")
        return

    print(f"Found {len(all_files)} transcripts. Starting parsing...\n")

    for idx, filename in enumerate(all_files):
        filepath = os.path.join(args.input_dir, filename)
        file_basename = os.path.splitext(filename)[0]

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw_text = f.read()

            # 1. Parse text to structured turns
            turns = parse_transcript_to_turns(raw_text)

            # 2. Save the parsed 'turns' dictionary as a JSON
            turns_out_path = os.path.join(args.output_dir, f"{file_basename}_turns.json")
            with open(turns_out_path, 'w', encoding='utf-8') as f:
                json.dump(turns, f, indent=2)

            # 3. Format back into a readable block with turn indexes
            full_text = get_segment_text(turns, 0, len(turns) - 1)

            # 4. Save the generated 'full_text' string to a txt file
            text_out_path = os.path.join(args.output_dir, f"{file_basename}_parsed.txt")
            with open(text_out_path, 'w', encoding='utf-8') as f:
                f.write(full_text)

            print(f"[{idx+1}/{len(all_files)}] Processed '{filename}' -> Extracted {len(turns)} turns.")

        except Exception as e:
            print(f"[ERROR] Failed to process '{filename}': {str(e)}")

if __name__ == "__main__":
    main()