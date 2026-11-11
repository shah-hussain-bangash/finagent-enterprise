"""Document intelligence building blocks (prompt §15; FR-013/014; DIR-014; SPR-017).

Pure, I/O-free pieces (format detection, typed extraction) plus ports with local and Azure
adapters (malware scanning, blob storage, OCR) and the isolated parser runner. Nothing here
executes macros, scripts or embedded content, and nothing reaches the network.
"""
