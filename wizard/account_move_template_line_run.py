from ast import literal_eval
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)


class AccountMoveTemplateLineRun(models.TransientModel):
    _name = "account.move.template.line.run"
    _description = "Wizard Lines to generate move from template"
    _inherit = ["analytic.mixin"]
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        comodel_name="account.move.template.run",
        ondelete="cascade",
    )
    name = fields.Char()
    sequence = fields.Integer()
    template_type = fields.Selection(
        selection=[
            ("input", "User input"),
            ("computed", "Computed"),
        ],
        string="Template Type",
        readonly=True,
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
    )
    price_unit = fields.Float(
        string="Unit Price",
        default=0.0,
    )
    amount = fields.Float()
    account_id = fields.Many2one(
        comodel_name="account.account",
    )
    tax_ids = fields.Many2many(
        comodel_name="account.tax",
        string="Taxes",
        readonly=True,
    )
    python_code = fields.Text(string="Formula", readonly=True)
    note = fields.Char()
    is_refund = fields.Boolean(string="Is a refund?", readonly=True)
