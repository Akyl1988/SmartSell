"""
PDF generation utilities for invoices and reports.
"""

import html
import os
from datetime import datetime

try:
    from weasyprint import HTML

    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Company, Order

logger = get_logger(__name__)


class PDFGenerator:
    """PDF generation service"""

    def __init__(self):
        self.output_dir = os.path.join(settings.UPLOAD_DIR, "pdfs")
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_invoice_pdf(
        self, order: Order, company: Company, template: str = "default"
    ) -> str:
        """Generate invoice PDF for order"""

        if WEASYPRINT_AVAILABLE:
            return await self._generate_invoice_weasyprint(order, company, template)
        elif REPORTLAB_AVAILABLE:
            return await self._generate_invoice_reportlab(order, company)
        else:
            raise Exception("No PDF library available. Install weasyprint or reportlab.")

    async def _generate_invoice_weasyprint(
        self, order: Order, company: Company, template: str = "default"
    ) -> str:
        """Generate invoice using WeasyPrint"""

        try:
            # Generate HTML content
            html_content = self._generate_invoice_html(order, company)

            # Generate filename
            filename = (
                f"invoice_{order.order_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            )
            file_path = os.path.join(self.output_dir, filename)

            # Generate PDF
            HTML(string=html_content).write_pdf(file_path)

            logger.info(f"Invoice PDF generated: {filename}")
            return file_path

        except Exception as e:
            logger.error(f"WeasyPrint PDF generation error: {e}")
            raise Exception(f"Failed to generate PDF: {e}")

    async def _generate_invoice_reportlab(self, order: Order, company: Company) -> str:
        """Generate invoice using ReportLab"""

        try:
            # Generate filename
            filename = (
                f"invoice_{order.order_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            )
            file_path = os.path.join(self.output_dir, filename)

            # Create PDF document
            doc = SimpleDocTemplate(
                file_path,
                pagesize=A4,
                rightMargin=2 * cm,
                leftMargin=2 * cm,
                topMargin=2 * cm,
                bottomMargin=2 * cm,
            )

            # Build content
            story = []
            styles = getSampleStyleSheet()

            # Header
            story.append(Paragraph(f"<b>{company.name}</b>", styles["Title"]))
            story.append(Spacer(1, 0.5 * cm))

            # Invoice info
            story.append(Paragraph(f"<b>Накладная №{order.order_number}</b>", styles["Heading2"]))
            story.append(
                Paragraph(f"Дата: {order.created_at.strftime('%d.%m.%Y')}", styles["Normal"])
            )
            story.append(Spacer(1, 0.5 * cm))

            # Customer info
            if order.customer_name or order.customer_phone:
                story.append(Paragraph("<b>Клиент:</b>", styles["Heading3"]))
                if order.customer_name:
                    story.append(Paragraph(f"Имя: {order.customer_name}", styles["Normal"]))
                if order.customer_phone:
                    story.append(Paragraph(f"Телефон: {order.customer_phone}", styles["Normal"]))
                story.append(Spacer(1, 0.5 * cm))

            # Items table
            table_data = [["Товар", "Количество", "Цена", "Сумма"]]

            for item in order.items:
                table_data.append(
                    [
                        item.name,
                        str(item.quantity),
                        f"{item.unit_price} {order.currency}",
                        f"{item.total_price} {order.currency}",
                    ]
                )

            # Add total row
            table_data.append(["", "", "Итого:", f"{order.total_amount} {order.currency}"])

            # Create table
            table = Table(table_data, colWidths=[8 * cm, 3 * cm, 3 * cm, 3 * cm])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 12),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -2), colors.beige),
                        ("BACKGROUND", (0, -1), (-1, -1), colors.lightgrey),
                        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ]
                )
            )

            story.append(table)
            story.append(Spacer(1, 1 * cm))

            # Footer
            story.append(Paragraph("Спасибо за покупку!", styles["Normal"]))

            # Build PDF
            doc.build(story)

            logger.info(f"Invoice PDF generated with ReportLab: {filename}")
            return file_path

        except Exception as e:
            logger.error(f"ReportLab PDF generation error: {e}")
            raise Exception(f"Failed to generate PDF: {e}")

    def _generate_invoice_html(self, order: Order, company: Company) -> str:
        """Generate HTML content for invoice"""

        items_html = ""
        for item in order.items:
            items_html += f"""
            <tr>
                <td>{html.escape(item.name)}</td>
                <td style="text-align: center;">{item.quantity}</td>
                <td style="text-align: right;">{item.unit_price} {html.escape(order.currency)}</td>
                <td style="text-align: right;">{item.total_price} {html.escape(order.currency)}</td>
            </tr>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Накладная №{html.escape(order.order_number)}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    font-size: 12px;
                    margin: 0;
                    padding: 20px;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                }}
                .company-name {{
                    font-size: 18px;
                    font-weight: bold;
                    margin-bottom: 10px;
                }}
                .invoice-title {{
                    font-size: 16px;
                    font-weight: bold;
                    margin-bottom: 20px;
                }}
                .info-section {{
                    margin-bottom: 20px;
                }}
                .customer-info {{
                    background-color: #f5f5f5;
                    padding: 10px;
                    margin-bottom: 20px;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 20px;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }}
                th {{
                    background-color: #f2f2f2;
                    font-weight: bold;
                }}
                .total-row {{
                    background-color: #f9f9f9;
                    font-weight: bold;
                }}
                .footer {{
                    text-align: center;
                    margin-top: 30px;
                    font-style: italic;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <div class="company-name">{html.escape(company.name)}</div>
                {f"<div>{html.escape(company.address)}</div>" if company.address else ""}
                {f"<div>Тел: {html.escape(company.phone)}</div>" if company.phone else ""}
            </div>

            <div class="invoice-title">Накладная №{html.escape(order.order_number)}</div>

            <div class="info-section">
                <div>Дата: {order.created_at.strftime("%d.%m.%Y %H:%M")}</div>
                <div>Статус: {html.escape(order.status)}</div>
            </div>

            {
            f'''
            <div class="customer-info">
                <strong>Информация о клиенте:</strong><br>
                {f"Имя: {html.escape(order.customer_name)}<br>" if order.customer_name else ""}
                {f"Телефон: {html.escape(order.customer_phone)}<br>" if order.customer_phone else ""}
                {f"Email: {html.escape(order.customer_email)}<br>" if order.customer_email else ""}
                {f"Адрес: {html.escape(order.customer_address)}<br>" if order.customer_address else ""}
            </div>
            '''
            if order.customer_name or order.customer_phone
            else ""
        }

            <table>
                <thead>
                    <tr>
                        <th>Товар</th>
                        <th style="width: 80px;">Кол-во</th>
                        <th style="width: 100px;">Цена</th>
                        <th style="width: 100px;">Сумма</th>
                    </tr>
                </thead>
                <tbody>
                    {items_html}
                    <tr class="total-row">
                        <td colspan="3" style="text-align: right;">Итого:</td>
                        <td style="text-align: right;">{order.total_amount} {
            order.currency
        }</td>
                    </tr>
                </tbody>
            </table>

            {
            f"<div><strong>Примечания:</strong> {order.notes}</div>"
            if order.notes
            else ""
        }

            <div class="footer">
                Спасибо за покупку!
            </div>
        </body>
        </html>
        """

        return html_content

    async def merge_pdfs(self, pdf_files: list[str], output_filename: str) -> str:
        """Merge multiple PDF files into one"""

        try:
            if not REPORTLAB_AVAILABLE:
                raise Exception("ReportLab is required for PDF merging")

            from PyPDF2 import PdfMerger

            output_path = os.path.join(self.output_dir, output_filename)

            merger = PdfMerger()

            for pdf_file in pdf_files:
                if os.path.exists(pdf_file):
                    merger.append(pdf_file)

            with open(output_path, "wb") as output_file:
                merger.write(output_file)

            merger.close()

            logger.info(f"Merged {len(pdf_files)} PDFs into {output_filename}")
            return output_path

        except Exception as e:
            logger.error(f"PDF merge error: {e}")
            raise Exception(f"Failed to merge PDFs: {e}")


# Global PDF generator instance
pdf_generator = PDFGenerator()


async def generate_invoice_pdf(order: Order, company: Company) -> str:
    """Generate invoice PDF (convenience function)"""
    return await pdf_generator.generate_invoice_pdf(order, company)


async def export_analytics_to_pdf(export_type: str, company_id: int, filters: dict, db) -> str:
    """Export analytics data to PDF"""

    try:
        filename = f"analytics_{export_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        file_path = os.path.join(pdf_generator.output_dir, filename)

        # Generate content based on export type
        if export_type == "sales":
            content = await _generate_sales_pdf_content(company_id, filters, db)
        elif export_type == "orders":
            content = await _generate_orders_pdf_content(company_id, filters, db)
        else:
            raise Exception(f"Unsupported export type: {export_type}")

        # Generate PDF with WeasyPrint if available
        if WEASYPRINT_AVAILABLE:
            HTML(string=content).write_pdf(file_path)
        else:
            raise Exception("WeasyPrint is required for analytics PDF export")

        logger.info(f"Analytics PDF exported: {filename}")
        return file_path

    except Exception as e:
        logger.error(f"Analytics PDF export error: {e}")
        raise Exception(f"Failed to export analytics PDF: {e}")


async def _generate_sales_pdf_content(company_id: int, filters: dict, db) -> str:
    """Generate HTML content for sales analytics PDF"""

    # TODO: Implement sales analytics content generation
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Отчет по продажам</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            h1 { color: #333; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>Отчет по продажам</h1>
        <p>Данные аналитики будут здесь...</p>
    </body>
    </html>
    """

    return html_content


async def _generate_orders_pdf_content(company_id: int, filters: dict, db) -> str:
    """Generate HTML content for orders analytics PDF"""

    # TODO: Implement orders analytics content generation
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Отчет по заказам</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            h1 { color: #333; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
        </style>
    </head>
    <body>
        <h1>Отчет по заказам</h1>
        <p>Данные аналитики будут здесь...</p>
    </body>
    </html>
    """

    return html_content
