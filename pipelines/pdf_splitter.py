import os, re, pathlib
from pypdf import PdfReader, PdfWriter

def split_syllabus_by_subject(pdf_path, output_folder="split_subjects"):
    os.makedirs(output_folder, exist_ok=True)
    reader = PdfReader(pdf_path)
    
    # Matches codes with or without spaces, e.g., 18CSE04, 18MTO 05, 22CSC15N
    code_pattern = re.compile(r'\b\d{2}[A-Z]{2,4}\s?[0-9]{2,3}[A-Z]?\b', re.IGNORECASE)
    
    subjects = []
    current_subject = None

    for page_num, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text_upper = text.upper()
        
        # Check for course code match and course structure markers
        code_match = code_pattern.search(text)
        has_objectives = "COURSE OBJECTIVES" in text_upper
        has_instruction = "INSTRUCTION" in text_upper
        is_index_page = "SCHEME OF INSTRUCTION" in text_upper or "SCHEME OF EXAMINATION" in text_upper
        
        is_subject_start = code_match and (has_objectives or has_instruction) and not is_index_page
        
        if is_subject_start:
            course_code = code_match.group(0).strip().replace(" ", "")
            
            # Save the previous subject group before starting a new one
            if current_subject:
                subjects.append(current_subject)
            
            # Extract subject title line right after or around the course code
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            subject_title = course_code
            
            for i, line in enumerate(lines):
                if code_match.group(0) in line:
                    # Look ahead for the subject name line
                    for next_line in lines[i+1 : i+4]:
                        if not any(k in next_line.upper() for k in ["INSTRUCTION", "CBIT", "WITH EFFECT", "DURATION"]):
                            subject_title = next_line
                            break
                    break

            current_subject = {
                "code": course_code,
                "title": subject_title,
                "pages": [page_num]
            }
        elif current_subject:
            current_subject["pages"].append(page_num)

    if current_subject:
        subjects.append(current_subject)

    # Export split PDFs
    for sub in subjects:
        writer = PdfWriter()
        for p in sub["pages"]:
            writer.add_page(reader.pages[p])
            
        clean_title = re.sub(r'[^\w\s-]', '', sub["title"]).strip().replace(' ', '_')
        output_filename = f"{sub['code']}_{clean_title}.pdf"
        output_filepath = os.path.join(output_folder, output_filename)
        
        with open(output_filepath, "wb") as output_file:
            writer.write(output_file)
            
        print(f"Generated: {output_filename} (Pages {sub['pages'][0] + 1} to {sub['pages'][-1] + 1})")

def get_regulation_code(name: str):
    regulation_code = ""
    i = 0
    while i < len(name) and name[i] != '-':
        regulation_code += name[i]
        i += 1
    return regulation_code

if __name__ == "__main__":
    for path in pathlib.Path('/Users/anuragmac/Documents/projects/smart_question_back_project/backend/pipelines/topic_pdfs/').glob("*.pdf"):
        regulation_code = get_regulation_code(path.stem)
        split_syllabus_by_subject(pdf_path=path, output_folder=f'split_subjects/{regulation_code}')