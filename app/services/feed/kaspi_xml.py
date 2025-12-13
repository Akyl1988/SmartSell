from __future__ import annotations
from typing import Iterable, Optional
from xml.etree.ElementTree import Element, SubElement, ElementTree

class KaspiXmlBuilder:
    """
    Минимальный строитель XML-фида для Kaspi.
    Предполагает, что элементы (offers) уже прошли фильтрацию и валидацию.
    """
    def __init__(self, shop_name: str = "SmartSell"):
        self.shop_name = shop_name

    def build(self, offers: Iterable[dict]) -> ElementTree:
        yml_catalog = Element("yml_catalog", date="2025-01-01 00:00")
        shop = SubElement(yml_catalog, "shop")
        SubElement(shop, "name").text = self.shop_name
        SubElement(shop, "company").text = self.shop_name

        categories = SubElement(shop, "categories")
        # при необходимости можно выгружать иерархию категорий
        # пока опустим — Kaspi принимает базовый фид и без категорий

        offers_el = SubElement(shop, "offers")
        for it in offers:
            o = SubElement(offers_el, "offer", id=str(it["sku"]))
            SubElement(o, "model").text = it.get("title") or ""
            SubElement(o, "brand").text = it.get("brand") or ""
            SubElement(o, "price").text = f'{it.get("price") or 0:.2f}'
            SubElement(o, "available").text = "true" if it.get("available", True) else "false"
            # при необходимости дополняем атрибутами, описанием, картинками и т.п.

        return ElementTree(element=yml_catalog)

    def save(self, tree: ElementTree, out_path: str) -> None:
        tree.write(out_path, encoding="utf-8", xml_declaration=True)
