import sys
import os

# Add src to PYTHONPATH
sys.path.append(os.path.join(os.getcwd(), 'src'))

from history_maker import nomi, identita, paleografia
from history_maker.menzioni import Menzione

def test_compound_names():
    print("Testing compound names...")
    s1 = "Domenico Antonio"
    s2 = "Domenicantonio"
    sim = nomi.somiglianza_nome(s1, s2)
    print(f"Similarity between '{s1}' and '{s2}': {sim}")
    assert sim > 0.8, f"Expected high similarity for {s1} and {s2}, got {sim}"

def test_missing_name_veto():
    print("\nTesting missing name veto...")
    # Create a persona with a first name
    p = identita.Persona(id=1)
    p._nomi["Giuseppe"] = 1
    p._nomi_canonici.add(paleografia.normalizza("Giuseppe"))
    p._cognomi_canonici.add(paleografia.normalizza("Lella"))
    
    # Mock Menzione with all required arguments
    m = Menzione(
        id=1, atto=1, tipo_atto="nascita", anno=1850, data=None, ruolo="testimone",
        nome=None, cognome="Lella", nome_letto=None, cognome_letto="Lella",
        cognome_origine="Lella", incerto=False, eta_letta=None, professione="Contadino",
        residenza=None, via=None, stato_vitale=None, note=None, immagine=None
    )
    
    # Mock ChiaviFamiliari
    class MockChiavi:
        def padre(self, m): return set()
        def madre(self, m): return set()
        def coniuge(self, m): return set()
    
    score = identita.punteggio(p, m, MockChiavi())
    print(f"Score for missing first name: {score}")
    assert score is not None, "Score should not be None when first name is missing"

if __name__ == "__main__":
    try:
        test_compound_names()
        test_missing_name_veto()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        import traceback
        traceback.print_exc()
