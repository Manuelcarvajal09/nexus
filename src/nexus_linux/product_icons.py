"""Small consistent stroke icon family; independent of installed desktop themes."""
from PySide6.QtCore import QByteArray,Qt
from PySide6.QtGui import QIcon,QPixmap,QPainter
from PySide6.QtSvg import QSvgRenderer
_CACHE={}
_SHAPES={
 'pin':'<path d="m9 3 6 0-1 6 4 4H6l4-4zM12 13v8"/>',
 'text':'<path d="M5 6h14M5 10h14M5 14h10M5 18h7"/>',
 'image':'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m4 18 5-5 4 3 4-6 3 8"/>',
 'file':'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6"/>',
 'url':'<path d="m10 13 4-4M8 15l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 9l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 -1) scale(.9)"/>',
}
def icon(kind,colour,size=20):
    key=(kind,colour,size)
    if key in _CACHE:return _CACHE[key]
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{colour}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{_SHAPES.get(kind,_SHAPES["text"])}</svg>'
    pixmap=QPixmap(size,size);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);QSvgRenderer(QByteArray(svg.encode())).render(painter);painter.end()
    if len(_CACHE)>64:_CACHE.clear()
    result=QIcon(pixmap);_CACHE[key]=result;return result
