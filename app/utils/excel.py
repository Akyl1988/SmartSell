"""
Excel import/export utilities for products and analytics.
"""

import os
from datetime import datetime
from typing import Any

import pandas as pd
from fastapi import UploadFile
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Order, Product, ProductStock

logger = get_logger(__name__)


class ExcelProcessor:
    """Excel processing service"""

    def __init__(self):
        self.output_dir = os.path.join(settings.UPLOAD_DIR, "excel")
        os.makedirs(self.output_dir, exist_ok=True)

    async def import_products_from_excel(
        self, file: UploadFile, company_id: int, db: AsyncSession
    ) -> dict[str, Any]:
        """Import products from Excel file"""

        try:
            # Read Excel file
            contents = await file.read()
            df = pd.read_excel(contents)

            # Validate required columns
            required_columns = ["sku", "name", "price"]
            missing_columns = [col for col in required_columns if col not in df.columns]

            if missing_columns:
                raise Exception(f"Missing required columns: {', '.join(missing_columns)}")

            results = {"total_rows": len(df), "created": 0, "updated": 0, "errors": []}

            for index, row in df.iterrows():
                try:
                    # Validate and clean data
                    sku = str(row["sku"]).strip()
                    name = str(row["name"]).strip()
                    price = float(row["price"])

                    if not sku or not name or price <= 0:
                        results["errors"].append(f"Row {index + 2}: Invalid data")
                        continue

                    # Check if product exists
                    result = await db.execute(
                        select(Product).where(
                            and_(
                                Product.company_id == company_id,
                                Product.sku == sku,
                                not Product.is_deleted,
                            )
                        )
                    )
                    existing_product = result.scalar_one_or_none()

                    if existing_product:
                        # Update existing product
                        existing_product.name = name
                        existing_product.price = price

                        # Update optional fields
                        if "category" in row and pd.notna(row["category"]):
                            existing_product.category = str(row["category"]).strip()

                        if "brand" in row and pd.notna(row["brand"]):
                            existing_product.brand = str(row["brand"]).strip()

                        if "description" in row and pd.notna(row["description"]):
                            existing_product.description = str(row["description"]).strip()

                        results["updated"] += 1
                    else:
                        # Create new product
                        product_data = {
                            "company_id": company_id,
                            "sku": sku,
                            "name": name,
                            "price": price,
                        }

                        # Add optional fields
                        if "category" in row and pd.notna(row["category"]):
                            product_data["category"] = str(row["category"]).strip()

                        if "brand" in row and pd.notna(row["brand"]):
                            product_data["brand"] = str(row["brand"]).strip()

                        if "description" in row and pd.notna(row["description"]):
                            product_data["description"] = str(row["description"]).strip()

                        if "kaspi_product_id" in row and pd.notna(row["kaspi_product_id"]):
                            product_data["kaspi_product_id"] = str(row["kaspi_product_id"]).strip()

                        product = Product(**product_data)
                        db.add(product)
                        results["created"] += 1

                    # Handle stock if quantity column exists
                    if "quantity" in row and pd.notna(row["quantity"]):
                        quantity = int(row["quantity"])
                        await self._update_product_stock(
                            db, existing_product or product, quantity, company_id
                        )

                except Exception as e:
                    results["errors"].append(f"Row {index + 2}: {str(e)}")

            await db.commit()

            logger.info(f"Product import completed: {results}")
            return results

        except Exception as e:
            logger.error(f"Excel import error: {e}")
            raise Exception(f"Failed to import products: {e}")

    async def export_products_to_excel(
        self, products: list[Product], include_stock: bool = True
    ) -> str:
        """Export products to Excel file"""

        try:
            # Prepare data
            data = []

            for product in products:
                row = {
                    "ID": product.id,
                    "SKU": product.sku,
                    "Название": product.name,
                    "Описание": product.description or "",
                    "Категория": product.category or "",
                    "Бренд": product.brand or "",
                    "Цена": float(product.price),
                    "Мин. цена": float(product.min_price) if product.min_price else "",
                    "Макс. цена": float(product.max_price) if product.max_price else "",
                    "Активен": "Да" if product.is_active else "Нет",
                    "Скрыт": "Да" if product.is_hidden else "Нет",
                    "Демпинг": "Да" if product.enable_dumping else "Нет",
                    "Предзаказ": "Да" if product.enable_preorder else "Нет",
                    "Kaspi ID": product.kaspi_product_id or "",
                    "Kaspi наличие": product.kaspi_availability,
                    "Создан": product.created_at.strftime("%d.%m.%Y %H:%M"),
                    "Обновлен": product.updated_at.strftime("%d.%m.%Y %H:%M"),
                }

                if include_stock:
                    row["Общий остаток"] = product.total_stock
                    row["Доступно"] = product.available_stock

                data.append(row)

            # Create DataFrame
            df = pd.DataFrame(data)

            # Generate filename
            filename = f"products_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            file_path = os.path.join(self.output_dir, filename)

            # Save to Excel
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Товары", index=False)

                # Auto-adjust column widths
                worksheet = writer.sheets["Товары"]
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter

                    for cell in column:
                        try:
                            if len(str(cell.value)) > max_length:
                                max_length = len(str(cell.value))
                        except (TypeError, AttributeError):
                            pass

                    adjusted_width = min(max_length + 2, 50)
                    worksheet.column_dimensions[column_letter].width = adjusted_width

            logger.info(f"Products exported to Excel: {filename}")
            return file_path

        except Exception as e:
            logger.error(f"Excel export error: {e}")
            raise Exception(f"Failed to export products: {e}")

    async def export_orders_to_excel(self, orders: list[Order], include_items: bool = True) -> str:
        """Export orders to Excel file"""

        try:
            # Prepare orders data
            orders_data = []
            items_data = []

            for order in orders:
                order_row = {
                    "ID": order.id,
                    "Номер заказа": order.order_number,
                    "Внешний ID": order.external_id or "",
                    "Источник": order.source,
                    "Статус": order.status,
                    "Клиент": order.customer_name or "",
                    "Телефон": order.customer_phone or "",
                    "Email": order.customer_email or "",
                    "Адрес": order.customer_address or "",
                    "Сумма": float(order.total_amount),
                    "Валюта": order.currency,
                    "Доставка": order.delivery_method or "",
                    "Адрес доставки": order.delivery_address or "",
                    "Примечания": order.notes or "",
                    "Создан": order.created_at.strftime("%d.%m.%Y %H:%M"),
                    "Обновлен": order.updated_at.strftime("%d.%m.%Y %H:%M"),
                }
                orders_data.append(order_row)

                # Add order items
                if include_items:
                    for item in order.items:
                        item_row = {
                            "ID заказа": order.id,
                            "Номер заказа": order.order_number,
                            "SKU": item.sku,
                            "Название": item.name,
                            "Цена": float(item.unit_price),
                            "Количество": item.quantity,
                            "Сумма": float(item.total_price),
                            "Примечания": item.notes or "",
                        }
                        items_data.append(item_row)

            # Create DataFrames
            orders_df = pd.DataFrame(orders_data)

            # Generate filename
            filename = f"orders_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            file_path = os.path.join(self.output_dir, filename)

            # Save to Excel
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                orders_df.to_excel(writer, sheet_name="Заказы", index=False)

                if include_items and items_data:
                    items_df = pd.DataFrame(items_data)
                    items_df.to_excel(writer, sheet_name="Позиции заказов", index=False)

                # Auto-adjust column widths for both sheets
                for sheet_name in writer.sheets:
                    worksheet = writer.sheets[sheet_name]
                    for column in worksheet.columns:
                        max_length = 0
                        column_letter = column[0].column_letter

                        for cell in column:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except (TypeError, AttributeError):
                                pass

                        adjusted_width = min(max_length + 2, 50)
                        worksheet.column_dimensions[column_letter].width = adjusted_width

            logger.info(f"Orders exported to Excel: {filename}")
            return file_path

        except Exception as e:
            logger.error(f"Orders Excel export error: {e}")
            raise Exception(f"Failed to export orders: {e}")

    async def _update_product_stock(
        self, db: AsyncSession, product: Product, quantity: int, company_id: int
    ):
        """Update product stock during import"""

        try:
            # Get main warehouse (or create if doesn't exist)
            from app.models import Warehouse

            result = await db.execute(
                select(Warehouse).where(and_(Warehouse.company_id == company_id, Warehouse.is_main))
            )
            warehouse = result.scalar_one_or_none()

            if not warehouse:
                # Create main warehouse
                warehouse = Warehouse(company_id=company_id, name="Основной склад", is_main=True)
                db.add(warehouse)
                await db.flush()

            # Get or create stock record
            result = await db.execute(
                select(ProductStock).where(
                    and_(
                        ProductStock.product_id == product.id,
                        ProductStock.warehouse_id == warehouse.id,
                    )
                )
            )
            stock = result.scalar_one_or_none()

            if stock:
                stock.quantity = quantity
            else:
                stock = ProductStock(
                    product_id=product.id, warehouse_id=warehouse.id, quantity=quantity
                )
                db.add(stock)

        except Exception as e:
            logger.error(f"Stock update error: {e}")


# Global Excel processor instance
excel_processor = ExcelProcessor()


async def import_products_from_excel(
    file: UploadFile, company_id: int, db: AsyncSession
) -> dict[str, Any]:
    """Import products from Excel (convenience function)"""
    return await excel_processor.import_products_from_excel(file, company_id, db)


async def export_products_to_excel(products: list[Product]) -> str:
    """Export products to Excel (convenience function)"""
    return await excel_processor.export_products_to_excel(products)


async def export_analytics_to_excel(
    export_type: str, company_id: int, filters: dict, db: AsyncSession
) -> str:
    """Export analytics data to Excel"""

    try:
        if export_type == "sales":
            return await _export_sales_analytics(company_id, filters, db)
        elif export_type == "orders":
            return await _export_orders_analytics(company_id, filters, db)
        elif export_type == "products":
            return await _export_products_analytics(company_id, filters, db)
        elif export_type == "customers":
            return await _export_customers_analytics(company_id, filters, db)
        else:
            raise Exception(f"Unsupported export type: {export_type}")

    except Exception as e:
        logger.error(f"Analytics Excel export error: {e}")
        raise Exception(f"Failed to export analytics: {e}")


async def _export_sales_analytics(company_id: int, filters: dict, db: AsyncSession) -> str:
    """Export sales analytics to Excel"""

    # TODO: Implement sales analytics export
    filename = f"sales_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join(excel_processor.output_dir, filename)

    # Create sample data
    data = [
        {"Дата": "2024-01-01", "Продажи": 150000, "Заказы": 25},
        {"Дата": "2024-01-02", "Продажи": 120000, "Заказы": 20},
    ]

    df = pd.DataFrame(data)
    df.to_excel(file_path, sheet_name="Аналитика продаж", index=False)

    return file_path


async def _export_orders_analytics(company_id: int, filters: dict, db: AsyncSession) -> str:
    """Export orders analytics to Excel"""

    # TODO: Implement orders analytics export
    filename = f"orders_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join(excel_processor.output_dir, filename)

    # Create sample data
    data = [
        {"Статус": "completed", "Количество": 100, "Сумма": 500000},
        {"Статус": "pending", "Количество": 25, "Сумма": 125000},
    ]

    df = pd.DataFrame(data)
    df.to_excel(file_path, sheet_name="Аналитика заказов", index=False)

    return file_path


async def _export_products_analytics(company_id: int, filters: dict, db: AsyncSession) -> str:
    """Export products analytics to Excel"""

    # TODO: Implement products analytics export
    filename = f"products_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join(excel_processor.output_dir, filename)

    # Create sample data
    data = [
        {"Товар": "iPhone 15", "Продано": 50, "Выручка": 2500000},
        {"Товар": "Samsung Galaxy", "Продано": 30, "Выручка": 1200000},
    ]

    df = pd.DataFrame(data)
    df.to_excel(file_path, sheet_name="Аналитика товаров", index=False)

    return file_path


async def _export_customers_analytics(company_id: int, filters: dict, db: AsyncSession) -> str:
    """Export customers analytics to Excel"""

    # TODO: Implement customers analytics export
    filename = f"customers_analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join(excel_processor.output_dir, filename)

    # Create sample data
    data = [
        {"Телефон": "+77001234567", "Заказы": 5, "Сумма": 250000, "Тип": "Постоянный"},
        {"Телефон": "+77009876543", "Заказы": 1, "Сумма": 50000, "Тип": "Новый"},
    ]

    df = pd.DataFrame(data)
    df.to_excel(file_path, sheet_name="Аналитика клиентов", index=False)

    return file_path
