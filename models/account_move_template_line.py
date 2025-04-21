from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.translate import _


class AccountMoveTemplateLine(models.Model):
    _name = "account.move.template.line"
    _description = "Journal Item Template"
    _inherit = ["analytic.mixin"]
    _order = "sequence, id"
    _check_company_auto = True

    template_id = fields.Many2one(
        comodel_name="account.move.template",
        string="Move Template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    move_type = fields.Selection(related="template_id.move_type")
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain=["|", ("parent_id", "=", False), ("is_company", "=", True)],
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        check_company=True,
        domain=[("deprecated", "=", False), ("account_type", "!=", "off_balance")],
        readonly=False,
        store=False,
    )
    account_code = fields.Char(
        string="Accounts Prefix",
        required=True,
        help="When creating a new journal item an account having this prefix"
        "will be looked for",
    )
    name = fields.Char(string="Label")
    sequence = fields.Integer(default=10)
    type = fields.Selection(
        [
            ("input", "User input"),
            ("computed", "Computed"),
        ],
        required=True,
        default="input",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        check_company=True,
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Unit of Measure",
        compute="_compute_product_uom_id",
        store=True,
        readonly=False,
    )
    quantity = fields.Float(
        string="Quantity",
        digits="Product Unit of Measure",
        default=1.0,
    )
    price_unit = fields.Float(
        string="Unit Price",
        digits="Product Price",
    )
    discount = fields.Float(
        string="Discount (%)",
        digits="Discount",
    )
    balance = fields.Float(
        string="Balance",
        digits="Product Price",
    )
    tax_ids = fields.Many2many(
        comodel_name="account.tax",
        string="Taxes",
        check_company=True,
    )
    python_code = fields.Text(string="Formula")
    note = fields.Char()

    _sql_constraints = [
        (
            "sequence_template_uniq",
            "UNIQUE(template_id, sequence)",
            "The sequence of the line must be unique per template!",
        ),
    ]

    @api.constrains("type", "python_code")
    def _check_python_code(self):
        for line in self:
            if line.type == "computed" and not line.python_code:
                raise ValidationError(
                    _("Python Code must be set for computed line with sequence %d.")
                    % line.sequence
                )

    @api.depends("product_id")
    def _compute_product_uom_id(self):
        for line in self:
            if line.product_id:
                line.product_uom_id = line.product_id.uom_id
            else:
                line.product_uom_id = False

    # @api.onchange("product_id")
    # def _onchange_product_id(self):
    #     """Actualiza campos relacionados cuando cambia el producto"""
    #     for line in self:
    #         if line.product_id:
    #             accounts = line.product_id.product_tmpl_id.get_product_accounts(
    #                 fiscal_pos=None
    #             )
    #             if accounts.get("expense") and line.move_line_type == "dr":
    #                 line.account_id = accounts["expense"].id
    #             elif accounts.get("income") and line.move_line_type == "cr":
    #                 line.account_id = accounts["income"].id

    @api.onchange("account_id")
    def _onchange_account_id(self):
        if not self.account_id:
            return

        self.account_code = self.account_id.code_store
        self.account_id = False
