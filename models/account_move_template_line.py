from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountMoveTemplateLine(models.Model):
    _name = "account.move.template.line"
    _description = "Journal Item Template"
    _order = "sequence, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        related="template_id.company_id",
        store=True,
    )
    template_id = fields.Many2one(
        comodel_name="account.move.template",
        string="Move Template",
        ondelete="cascade",
        index=True,
    )
    name = fields.Char(string="Label")
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain="['|', ('parent_id', '=', False), ('is_company', '=', True)]",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        required=True,
        check_company=True,
        domain="[('deprecated', '=', False), ('account_type', '!=', 'off_balance')]",
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
    amount = fields.Float(default=0.0)
    tax_ids = fields.Many2many(
        comodel_name="account.tax",
        string="Taxes",
        check_company=True,
    )
    move_line_type = fields.Selection(
        selection=[("cr", "Credit"), ("dr", "Debit")],
        string="Direction",
        required=True,
    )

    name = fields.Char(string="Label")
    sequence = fields.Integer(required=True)
    account_prefix = fields.Char(
        string='Accounts Prefix',
        help="When creating a new journal item an account having this prefix"
             "will be looked for",
    )
    move_line_type = fields.Selection(
        [
            ("cr", "Credit"),
            ("dr", "Debit")
        ],
        string="Direction",
        required=True,
    )
    type = fields.Selection(
        [
            ("input", "User input"),
            ("computed", "Computed"),
        ],
        required=True,
        default="input",
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
                
    @api.onchange("product_id")
    def _onchange_product_id(self):
        """Actualiza campos relacionados cuando cambia el producto"""
        for line in self:
            if line.product_id:
                accounts = line.product_id.product_tmpl_id.get_product_accounts(fiscal_pos=None)
                if accounts.get('expense') and line.move_line_type == 'dr':
                    line.account_id = accounts['expense'].id
                elif accounts.get('income') and line.move_line_type == 'cr':
                    line.account_id = accounts['income'].id