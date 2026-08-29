"""History Maker: dal Portale Antenati alla storia di Torrebruna (1809-1900).

La pipeline ha quattro fasi indipendenti, ognuna con il proprio comando
CLI e il proprio artefatto su disco, cosi' che una fase possa essere
ripetuta senza rifare le precedenti:

1. ``discover``    Selenium naviga il portale (protetto da WAF) e raccoglie
                   l'elenco dei registri di Torrebruna -> ``catalogo.json``
2. ``download``    HTTP semplice sugli endpoint IIIF (non protetti) scarica
                   le immagini -> ``data/immagini/``
3. ``transcribe``  Claude legge le immagini e restituisce gli atti in JSON
                   -> ``data/trascrizioni/``
4. ``dataset``     Le trascrizioni diventano un database consultabile
                   -> ``data/dataset/``
"""

__version__ = "0.1.0"
