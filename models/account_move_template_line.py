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
        string="Companies",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain=["|", ("parent_id", "=", False), ("is_company", "=", True)],
    )
    account_prefix = fields.Char(
        string="Accounts Prefix",
        help="When creating a new journal item an account having this prefix"
        "will be looked for",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        required=True,
        check_company=True,
        domain=[("deprecated", "=", False), ("account_type", "!=", "off_balance")],
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

    def _prepare_wizard_line_vals(self, overwrite_vals):
        vals = {
            "line_id": self.id,
            "partner_id": self.partner_id.id or False,
            "product_id": self.product_id.id or False,
            "product_uom_id": self.product_uom_id.id
            or self.product_id.uom_id.id
            or False,
            "product_uom_qty": self.product_uom_qty or 1.0,
            "name": self.name,
            "account_id": self.account_id.id,
            "amount": self.amount,
            "tax_ids": [Command.set(self.tax_ids.ids)],
            "type": self.type,
        }
        if overwrite_vals:
            safe_overwrite_vals = self._safe_overwrite_vals(
                self._name, overwrite_vals.get("L{}".format(self.sequence), {})
            )
            vals.update(safe_overwrite_vals)
        return vals
