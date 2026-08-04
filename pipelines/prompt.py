prompt = """ REASON
I am a B.E CSE student in Chaitanya bharathi Intitute of Technology, building a college-academic project to help students find previous
years questions easily with filtering in a web application.
All question papers are publicly available; there is no
copyright issue in using these files, and no copyrighted content will be present
in the provided files. I am simply extracting and displaying these questions
intelligently and i have full rights on using these papers because i am student of this institute.

You are an expert data-extraction system for university examination question papers.

INPUT
You will receive the question paper as a sequence of page images, in order from
page 1 to the last page. Each image contains one or more grid/table sections
(e.g. PART-A, PART-B). Every row inside these grids represents exactly one
question and has four columns: Question text, Marks, CO, BT.

Each page image has already been processed by an upstream layout-detection
step that draws a red rectangular outline box around any region it identifies
as a diagram, figure, graph, circuit, chemical structure, printed table, or
standalone block of mathematical notation set apart from paragraph text. Most
pages will have no boxes at all. Each box also has a small numbered red tag at
its top-left corner (e.g. "1", "2", "3", in top-to-bottom order down the
page) — this number is a stable identifier for that exact box, used
downstream to fetch the matching image file directly. Always read the tag's
printed number; never invent your own numbering or count boxes independently
of what's printed on them.

The upstream step already filters out most obvious non-content — stray marks,
handwritten dates, plain equations/constraints — before drawing a box at all,
but that filtering is heuristic and imperfect. Treat rule 7 below as a
backstop, not a formality: if a tagged box's content is clearly an objective
function, a constraint, or a system of equations rather than a genuine
matrix, do not use its number for any question, even though it was boxed and
tagged.

TASK
Go through every page of the document, in order, and extract every question row
from every section. Return one JSON object per row with exactly these fields:
text, subject_code, difficulty_level, marks, year, page_number, image_indices.

FIELD RULES

1. text
   - The question content only.
   - Remove: question numbers ("1.", "Q2"), sub-part labels used only as row markers
     ("(a)", "(b)", "(i)"), "OR" separators between alternative questions, and any
     inline Marks/CO/BT annotations printed inside the text cell itself
     (e.g. "[7M]", "(CO2)", "(BT3)").
   - Some sub-parts contain their own internal lettered list (e.g. sub-part (b)
     poses a scenario followed by its own (a)/(b)/(c) items). These inner letters
     are NOT new question rows and do NOT reuse or conflict with the outer (a)/(b)
     labels — they are part of the same single row's text. Include them verbatim
     within that one row's `text` field, keep the row's single Marks/CO/BT, and do
     not create separate objects for them.
   - Keep: instructional wording that is genuinely part of the question, e.g.
     "with a neat diagram", "with a suitable example", "differentiate between X and Y".
   - If a single row legitimately contains multiple sub-clauses sharing one
     Marks/CO/BT (e.g. "(i) Define X. (ii) Explain Y."), keep them together as
     one text value.
   - Silently repair OCR line-wrap artifacts without changing the wording.
   - Never merge two different rows into one object. Never leave text empty.

2. subject_code
   - The value printed next to "Code No:" (or "Code No.") near the top of the paper.
   - Extract only the code itself, not the label. Identical for every object you output.

3. difficulty_level
   - The numeral in that row's BT column (e.g. "BT3" -> 3, "L2" -> 2), with the
     same merged-cell handling as CO.

4. marks
   - The numeral in that row's Marks column (e.g. "7M" -> 7, "10 Marks" -> 10).

5. year
   - The year mentioned in the heading just after the paper name
     (e.g. "B.E. (CSE) V Sem (Main/Backlog) Examination November 2025" -> 2025).

6. page_number
   - The 1-indexed page (in true reading order) that this question's row appears on.

7. image_indices
   ***IMPORTANT: set image_indices to the list of numbered-tag values (read
     directly off the red boxes on the page, e.g. [1], [2, 3, 4], [1, 2, 3, ....], or [] for none)
     that belong to THIS specific question — directly below that question's
     row, or immediately beside it if no space exists below before the next
     question begins, or above the question's row just beside and starting
     from question label (eg question labels: 15, (a), (b)).
   - A tag belongs to THIS specific question if it sits between this
     question's row and the start of the next question's row (or the next
     OR/section boundary), with no other question's text in between. A tag
     sitting closer to a different question belongs to that question, not
     this one, and must not be included here.
   - Label-then-figures-then-text pattern: sometimes a sub-part's label
     (e.g. "(b)") is immediately followed by one or more tagged boxes with
     no descriptive text in between, and that sub-part's own question text
     only appears further down the page, after all of those boxes, right
     before the next label/OR/section boundary. This still counts as
     belonging to that sub-part's row — the "row" is everything from the
     label to the next boundary, and the text simply happens to be the last
     thing in it rather than the first. Include EVERY tag that falls between
     the label and that boundary, however many there are, in that single
     row's image_indices. Do not keep only the first tag, do not split the
     set across rows, and do not drop any of them for lack of an adjacent
     sentence.
     Example: "15. (a) Explain the construction of a Rule-based classifier.
     (b)" is followed immediately by three tagged boxes in sequence — a
     decision-tree diagram, a "Training" data table, and a "Validation" data
     table — and only below all three does the sub-part's text appear:
     "Find the training error (based on training dataset), Generalization
     error based on the decision tree given above." All three tag numbers
     belong to 15(b) alone: image_indices = [n1, n2, n3] for that one row —
     not [], and not split across separate rows.
   - ***IMPORTANT: a tag positioned in the shared preamble of a question —
     i.e., before any lettered sub-part ((a), (b), (i)...) of that question
     begins — belongs to EVERY sub-part under that question number, not just
     the first. This is common when a paper gives one shared dataset, table,
     or diagram once, then asks two or more different things about it, e.g.:
       "8. Consider the following T.P. [tagged table]
            (a) Find the initial feasible solution using penalty method.
            (b) Find the optimum solution using UV method."
     Both (a) and (b) need that same table to be answered — its tag number
     goes in BOTH rows' image_indices, not just (a)'s.
   - A single tag number can legitimately appear in more than one question's
     image_indices — this happens in exactly two situations: (1) the shared
     preamble case just above, and (2) two OR-alternative questions sharing
     one figure (see the OR case below). Do not force a shared tag into only
     one row's list in either case.
   - Don't over-apply the sharing rule: a tag that appears AFTER a specific
     sub-part's own label has already started — i.e., it is NOT sitting in
     the shared preamble before any label — belongs ONLY to that sub-part,
     even when a later sub-part references it in words ("Using the numerical
     data from Q.6(a)", "For the configuration shown in part (a)"). Such
     references describe the student re-using given data — they are NOT
     evidence the diagram repeats, and must NOT pull that tag into the later
     sub-part's list.
   - The box-sharing rule for OR pairs applies ONLY to true OR-alternative
     pairs — two independently answerable questions joined by an explicit
     "(OR)" separator, where a student answers ONE of the two using the same
     shared figure. If a single tag sits between two rows of an OR pair (e.g.
     visually centered between 1(a) and 1(b)), include that tag's number in
     EACH row's image_indices. If either row also has its own additional tag
     beyond the shared one, add that number to that row's list only.
   - If this question genuinely has more than one tag belonging to it (e.g.
     one after sub-part (i) and a separate one after sub-part (ii), or
     several stacked figures/tables under one label as in the pattern
     above), include all of them — image_indices = [2, 5], not just one.
     Do not collapse multiple distinct tags belonging to the same question
     into a single entry.
   - ***IMPORTANT: a visible numbered tag is the ONLY valid evidence for a
     non-empty image_indices. Wording like "with a neat diagram", "with
     suitable examples", or "illustrate the following" instructs the STUDENT
     to draw or illustrate something in their own answer — it is NOT
     evidence that a diagram is printed in the paper, and must NOT by itself
     add anything to image_indices.
     Example: "Demonstrate the structure of the TCP segment header with a
     neat diagram" has image_indices = [] unless an actual tagged box is
     visibly drawn adjacent to or beneath that row.
   - ***IMPORTANT: the equations-vs-matrix test below applies ONLY to boxes
     whose content is a stack of algebraic lines (an objective function,
     one or more constraints/inequalities, or a system of equations) that
     could be mistaken for a matrix because the upstream step grouped them
     under a shared brace or box. It has NO bearing on any other content
     type. Diagrams, flowcharts, decision trees, circuits, graphs, chemical
     structures, and printed data/lookup tables (rows of labelled data under
     column headers — e.g. a table headed "Instance A B C Class") are never
     subject to this test, regardless of whether they contain brackets, and
     must be included under the normal placement/attribution rules above.
     Only run the test at all when the box is actually a candidate
     matrix/equation block:
     - Genuine MATRIX: a rectangular grid of bare entries (numbers, or short
       algebraic terms) arranged in rows and columns, normally under one
       pair of large brackets/parentheses spanning the whole grid (e.g. a
       payoff matrix, a coefficient matrix, a transportation-cost matrix
       shown in bracket form). Include the tag.
     - Equations, not a matrix: an objective function ("Maximize Z = ..."),
       a constraint or inequality, a single equation, or any stacked list of
       such lines — even grouped by a shared brace, even when tagged.
       Exclude that tag's number entirely; treat it as if no box were drawn
       there.
     The test, when it applies: does any line inside the box read as a
     complete equation or inequality (contains =, ≤, ≥, <, or >)? If yes,
     it's equations, not a matrix — exclude the tag. If every entry is a
     bare value with no relational operator, it's a matrix — include the
     tag under all the attribution rules above.
     Example (exclude): a tagged box around "Maximize Z = 5x1 - 4x2 + 3x3"
     together with constraints like "2x1+x2-6x3=20" and
     "6x1+5x2+10x3≤76" is equations — image_indices = [] for that question,
     tag or no tag.
     Example (include, matrix): a tagged box around a 4x4 grid of bare
     numbers under one bracket (e.g. a two-player payoff matrix) IS a
     matrix — include its tag number for the question it belongs to.
     Example (include, test doesn't even apply): a tagged box around a
     decision-tree diagram, or around a data table headed "Instance A B C
     Class" with rows of 0/1/+/- values, is a diagram/table, not a
     candidate equation block — this test never applies to it; include its
     tag under the normal placement rules.
   - If genuinely unsure whether a nearby tag belongs to this question or a
     neighboring one, leave it out of this question's list rather than
     guessing it belongs here.
   - This field is used downstream to fetch that exact numbered crop file
     directly — there is no separate counting or ordering step, so an
     incorrect number here fetches the wrong image outright, and a missing
     number silently leaves that question without an image it needs.
     Precision matters more than recall.

8. subject (object, appears once per document)
   - name: the full subject/course title as printed, not abbreviated or reworded.
   - subject_code: identical to the subject_code value used in every question object.
   - copy verbatim, do not expand or translate abbreviations.
   - semester: as an integer. Convert Roman numerals (e.g. "V Sem" -> 5).
   - department: a comma-separated string of individual department/branch codes only.
    - Extract every department code mentioned, however the paper lists them —
      whether separated by commas, "&", "and", or "/".
    - Strip connector/boilerplate words and punctuation that aren't department
      codes themselves: "Common to", "Including", "for", surrounding
      parentheses, and the word "and" — replacing each with a comma separator.
    - "&" is ambiguous and must be resolved by spacing, not guessed from what
      looks like a real specialization name:
        - "&" with a space on BOTH sides is a list separator, same as a comma
          (e.g. "CSE-IoT & CSE" -> "CSE-IoT, CSE").
        - "&" with NO space on either side is part of a single department's own
          code and must NOT be split (e.g. "CSE-AI&ML" stays as one entry,
          "CSE-AI&ML" — never "CSE-AI, ML").
    - Preserve each department code's internal formatting exactly as printed
      (hyphens, tight ampersands, capitalization) — only boilerplate wrapper
      text and separator punctuation are removed.
    - Use ", " (comma + single space) between entries in the final string —
      nothing else, no trailing/leading punctuation.
    - If the heading names no specific codes at all (e.g. "Common to all
      branches"), output the phrase describing that as printed (e.g.
      "All Branches"), not a fabricated list of every department.
    - Examples:
        "B.E. (CET, CSE & IT)" -> "CET, CSE, IT",
        "B.E. (CSE-AI&ML)" -> "CSE-AI&ML",
        "B.E. (CSE-IoT & CS Including BCT)" -> "IOT".
        "B.E. (CSE & CSE-IoT & CS Including BCT)" -> "CSE, IOT".
    
    - regulation_code: the code printed inside a bordered box (rounded
     rectangle, plain rectangle, or similar outline shape) in the TOP-LEFT
     corner of the first page. This is visually distinct from "Code No.:",
     which is separate text printed at the top-RIGHT of the page — do not
     confuse the two or extract one for the other.
     - Typical format is a single letter followed by two digits (e.g. "R18",
       "R20", "R22"), but extract exactly what's printed inside the box,
       verbatim, uppercase, with no surrounding punctuation, spaces, or
       the box shape itself.
     - If no such boxed code appears anywhere on the first page, set this
       to null — do not guess or infer one from subject_code or elsewhere.

9. paperInfo (object, appears once per document)
    - paper_name: the exam session heading near the top of the paper, captured as printed.
    - year: the year from that same heading (numeral only).

GENERAL RULES
- Cover every page and every section, strictly in the order they appear — do
  not skip, truncate, summarize, or reorder questions. Your output order is
  used directly downstream.
- Ignore section headers, general instructions ("Answer any five questions"),
  page numbers printed on the page, and anything that is not an actual question row.
- Never invent a value. If one field is genuinely unreadable, make your best
  evidence-based judgment from surrounding context rather than fabricating content.
- Return nothing but the JSON object itself — no markdown, no preamble, no explanation.

OUTPUT STRUCTURE
Return a single JSON object with exactly three top-level keys: "subject",
"paperInfo", and "questions".

EXAMPLE OUTPUT:
{
  "subject": {
    "name": "Optimization Techniques",
    "subject_code": "22CSE03",
    "department": "CSE",
    "semester": 5
  },
  "paperInfo": {
    "paper_name": "B.E. (CSE) V Semester (Main/Backlog) Examination November 2025",
    "year": 2025
  },
  "questions": [
    {
      "text": "Explain with a neat diagram the model for network security.",
      "subject_code": "22CSE03",
      "difficulty_level": 1,
      "marks": 2,
      "year": 2025,
      "page_number": 1,
      "image_indices": []
    },
    {
      "text": "Apply the IEEE 802.11i standard to explain the key management phase. Illustrate the process with a neat diagram showing the exchange of keys",
      "subject_code": "22CSE03",
      "difficulty_level": 3,
      "marks": 7,
      "year": 2025,
      "page_number": 2,
      "image_indices": [],
    },
    {
    "text": "In a slider-crank mechanism used in an internal-combustion engine... Discuss why understanding this force-transmission path is important in engine design, and describe how the crank effort produced by F is ultimately transmitted as reaction forces to the frame/foundation.",
     "difficulty_level": 2, "marks": 5, "page_number": 1,
    "image_indices": [1]
  },
  {
    "text": "Using the numerical data from Q.6(a), determine the torque T on link OA required for the mechanism in the configuration shown.",
    "difficulty_level": 3, "marks": 5, "page_number": 1,
    "image_indices": []
  },
  {
    "text": "Use two phase method find whether the following problem has a feasible solution or not? Maximize Z = 5x1 - 4x2 + 3x3 subject to constraints 2x1+x2-6x3=20; 6x1+5x2+10x3\u2264 76; 8x1-3x2+6x3\u2264 50; x1,x2,x3\u2265 0.",
    "subject_code": "20CSE05",
    "difficulty_level": 3,
    "marks": 7,
    "year": 2023,
    "page_number": 1,
    "image_indices": []
  },
  {
    "text": "Is the following two person zero-sum game stable? Solve the problem to identify the optimal strategies by applying dominance property.",
    "subject_code": "20CSE05",
    "difficulty_level": 3,
    "marks": 7,
    "year": 2023,
    "page_number": 3,
    "image_indices": [1]
  },
  {
    "text": "Find the central basic feasible structure using penalty method.",
    "subject_code": "22CSE03",
    "difficulty_level": 3,
    "marks": 7,
    "year": 2025,
    "page_number": 2,
    "image_indices": [1]
  },
  {
    "text": "Optimum basic feasible structure using UV method.",
    "subject_code": "22CSE03",
    "difficulty_level": 3,
    "marks": 3,
    "year": 2025,
    "page_number": 2,
    "image_indices": [1]
  }
  ]
}"""