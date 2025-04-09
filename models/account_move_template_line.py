from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountMoveTemplateLine(models.Model):
    _name = "account.move.template.line"
    _description = "Journal Item Template"
    _order = "sequence, id"
    _check_company_auto = True

    template_id = fields.Many2one(
        comodel_name="account.move.template",
        string="Move Template",
        ondelete="cascade",
    )
    name = fields.Char(string="Label")
    sequence = fields.Integer(required=True)
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain=["|", ("parent_id", "=", False), ("is_company", "=", True)],
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        required=True,
        check_company=True,
        domain=[("deprecated", "=", False), ("account_type", "!=", "off_balance")],
    )
    opt_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account if Negative",
        help="When amount is negative, use this account instead",
        check_company=True,
        domain=[("deprecated", "=", False), ("account_type", "!=", "off_balance")],
    )
    account_prefix = fields.Char(
        string="Account Prefix",
        help="When creating a new journal item, an account having this prefix will be looked for",
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
        precompute=True,
        readonly=False,
    )
    quantity = fields.Float(
        string="Quantity",
        digits="Product Unit of Measure",
    )
    product_uom_qty = fields.Float(
        string="Product Quantity",
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
    amount = fields.Float(default=0)
    tax_ids = fields.Many2many(
        comodel_name="account.tax",
        string="Taxes",
        check_company=True,
    )
    tax_line_id = fields.Many2one(
        "account.tax", 
        string="Originator Tax", 
        ondelete="restrict"
    )
    tax_repartition_line_id = fields.Many2one(
        "account.tax.repartition.line",
        string="Tax Repartition Line",
    )
    move_line_type = fields.Selection(
        [("cr", "Credit"), ("dr", "Debit")],
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
    payment_term_id = fields.Many2one(
        "account.payment.term", 
        string="Payment Terms",
        help="Used to compute the due date of the journal item."
    )
    is_refund = fields.Boolean(string="Is a refund?")

    _sequence_template_uniq = models.Constraint(
        "UNIQUE(template_id, sequence)",
        "The sequence of the line must be unique per template",
    )

    @api.constrains("type", "python_code")
    def check_python_code(self):
        for line in self:
            if line.type == "computed" and not line.python_code:
                raise ValidationError(
                    _("Python Code must be set for computed line with sequence %d.")
                    % line.sequence
                )

    @api.depends("product_id")
    def _compute_product_uom_id(self):
        for line in self:
            line.product_uom_id = line.product_id.uom_id

    def _safe_overwrite_vals(self, model, vals):
        obj = self.env[model]
        copy_vals = vals.copy()
        invalid_keys = list(
            set(list(vals.keys())) - set(list(dict(obj._fields).keys()))
        )
        for key in invalid_keys:
            copy_vals.pop(key)
        return copy_vals

    def _prepare_wizard_line_vals(self, overwrite_vals=None):
        vals = {
            "sequence": self.sequence,
            "name": self.name,
            "partner_id": self.partner_id.id or False,
            "account_id": self.account_id.id,
            "move_line_type": self.move_line_type,
            "amount": self.amount,
            "tax_ids": [(6, 0, self.tax_ids.ids)] if self.tax_ids else False,
            "tax_line_id": self.tax_line_id.id or False,
            "type": self.type,
            "note": self.note,
            "payment_term_id": self.payment_term_id.id or False,
        }
        if overwrite_vals:
            safe_overwrite_vals = self._safe_overwrite_vals(
                "account.move.template.line.run", 
                overwrite_vals.get("L{}".format(self.sequence), {})
            )
            vals.update(safe_overwrite_vals)
        return vals