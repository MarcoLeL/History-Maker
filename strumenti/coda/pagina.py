"""pagina.py ATTO [x0 y0 x1 y1 in 1600-scala] -> salva p_ATTO.jpg"""
import sys, sqlite3
from PIL import Image, ImageOps
S = str(__import__("pathlib").Path(__file__).resolve().parent) + "/"
c=sqlite3.connect('file:data/dataset/torrebruna.sqlite?mode=ro',uri=True,timeout=60)
a=int(sys.argv[1]); p=c.execute('select immagine from atti where id=?',(a,)).fetchone()[0]
im=Image.open('data/immagini/'+p).convert('L'); w,h=im.size; f=w/1600
if len(sys.argv)>2:
    x0,y0,x1,y1=[int(v) for v in sys.argv[2:6]]
    im=ImageOps.autocontrast(im.crop((int(x0*f),int(y0*f),int(x1*f),int(y1*f))),cutoff=1)
    k=min(2.0,1600/im.width); im=im.resize((int(im.width*k),int(im.height*k)))
    out=S+f'p_{a}_z.jpg'
else:
    im=im.resize((1600,int(1600*h/w))); out=S+f'p_{a}.jpg'
im.save(out); print(p,out)
