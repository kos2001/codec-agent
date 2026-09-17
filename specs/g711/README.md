# G.711 specification (not included)

ITU-T Recommendation G.711 (11/88), English PDF — freely downloadable from ITU but
"All rights reserved", so it is not committed here.

- Page: https://www.itu.int/rec/T-REC-G.711-198811-I/en
- Save as: `specs/g711/T-REC-G.711-198811-I-PDF-E.pdf`
- SHA-256: `4348b527fbb1488677503d3b0f65094fb8a4b01e159ce01d7adbc29d5efdc556`

ITU rejects plain HTTP clients (curl: "Request Rejected" / 500). Download it in a browser
from the page above. `tests/test_spec_tables.py::test_pdf_is_the_one_the_tests_were_written_against`
checks the hash, and skips (missing evidence, not a pass) when the file is absent.

Tables 1a/1b/2a/2b are images inside the PDF; their content is transcribed in
`tests/test_spec_tables.py`.
