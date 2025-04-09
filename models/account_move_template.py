from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval


class AccountMoveTemplate(models.Model):
    _name = "account.move.template"
    _description = "Journal Entry Template"
    _check_company_auto = True

    company_id = fields.Many2many(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        readonly=False,
    )
    company_currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_company_currency_id",
    )
    journal_id = fields.Many2one(
        'account.journal',
        string='Journal',
        compute='_compute_journal_id', store=True, readonly=False, precompute=True,
        required=True,
        check_company=True,
        domain="[('id', 'in', suitable_journal_ids)]",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Template Currency",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain=["|", ("parent_id", "=", False), ("is_company", "=", True)],
    )
    payment_term_id = fields.Many2one(
        comodel_name="account.payment.term",
        string="Payment Terms",
        help="Used to compute the due date of the journal item.",
    )
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    move_type = fields.Selection(
        selection=[
            ('entry', 'Journal Entry'),
            ('out_invoice', 'Customer Invoice'),
            ('out_refund', 'Customer Credit Note'),
            ('in_invoice', 'Vendor Bill'),
            ('in_refund', 'Vendor Credit Note'),
            ('out_receipt', 'Sales Receipt'),
            ('in_receipt', 'Purchase Receipt'),
        ],
        string="Type",
        default="entry",
        required=True,
    )
    ref = fields.Char(string="Reference", copy=False)
    line_ids = fields.One2many(
        comodel_name="account.move.template.line",
        inverse_name="template_id",
        string="Lines",
    )
    date = fields.Date(
        string='Date',
        index=True,
        compute='_compute_date', store=True, required=True, readonly=False, precompute=True,
        copy=False,
        tracking=True,
    )
    invoice_date = fields.Date(
        string='Invoice/Bill Date',
        index=True,
        copy=False,
    )
    origin_payment_id = fields.Many2one(  # the payment this is the journal entry of
        comodel_name='account.payment',
        string="Payment",
        index='btree_not_null',
        copy=False,
        check_company=True,
    )
    statement_line_id = fields.Many2one(
        comodel_name='account.bank.statement.line',
        string="Statement Line",
        copy=False,
        check_company=True,
        index='btree_not_null',
    )
    suitable_journal_ids = fields.Many2many(
        'account.journal',
        compute='_compute_suitable_journal_ids',
    )
    invoice_filter_type_domain = fields.Char(compute='_compute_invoice_filter_type_domain')


    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {}, name=_("%s (copy)") % self.name)
        return super().copy(default)

    @api.depends_context("company")
    def _compute_company_currency_id(self):
        self.company_currency_id = self.env.company.currency_id

    def eval_computed_line(self, line, sequence2amount):
        safe_eval_dict = {}
        for seq, amount in sequence2amount.items():
            safe_eval_dict["L%d" % seq] = amount
        try:
            val = safe_eval(line.python_code, safe_eval_dict)
            sequence2amount[line.sequence] = val
        except ValueError as err:
            raise UserError(
                _(
                    "Impossible to compute the formula of line with sequence %(sequence)s "
                    "(formula: %(code)s). Check that the lines used in the formula "
                    "really exists and have a lower sequence than the current "
                    "line.",
                    sequence=line.sequence,
                    code=line.python_code,
                )
            ) from err
        except SyntaxError as err:
            raise UserError(
                _(
                    "Impossible to compute the formula of line with sequence %(sequence)s "
                    "(formula: %(code)s): the syntax of the formula is wrong.",
                    sequence=line.sequence,
                    code=line.python_code,
                )
            ) from err

    def compute_lines(self, sequence2amount):
        company_cur = self.company_id.currency_id
        input_sequence2amount = sequence2amount.copy()
        for line in self.line_ids.filtered(lambda x: x.type == "input"):
            if line.sequence not in sequence2amount:
                raise UserError(
                    _(
                        "You deleted a line in the wizard. This is not allowed: "
                        "you should either update the template or modify the "
                        "journal entry that will be generated by this wizard."
                    )
                )
            input_sequence2amount.pop(line.sequence)
        if input_sequence2amount:
            raise UserError(
                _(
                    "You added a line in the wizard. This is not allowed: "
                    "you should either update the template or modify "
                    "the journal entry that will be generated by this wizard."
                )
            )
        for line in self.line_ids.filtered(lambda x: x.type == "computed"):
            self.eval_computed_line(line, sequence2amount)
            sequence2amount[line.sequence] = company_cur.round(
                sequence2amount[line.sequence]
            )
        return sequence2amount

    def prepare_wizard_values(self):
        vals = {
            "partner_id": self.partner_id.id or False,
            "journal_id": self.journal_id.id,
            "currency_id": self.currency_id.id,
            "move_type": self.move_type,
            "state": "set_lines",
            "ref": self.ref,
            "post": self.post,
        }
        if self._context.get("default_partner_id"):
            vals["partner_id"] = self._context.get("default_partner_id")
        if self.env.context.get("operation_id"):
            operation = self.env["account.move.operation"].browse(
                self.env.context.get("operation_id")
            )
            if not vals.get("currency_id") and operation.currency_id:
                vals["currency_id"] = operation.currency_id.id
            if not vals.get("partner_id"):
                vals["partner_id"] = operation.partner_id.id
            line = self.env["account.move.operation.line"].browse(
                self.env.context.get("operation_line_id")
            )
            if line and line.date_last_document:
                date_last_document = line._get_latest_document_date()
                if date_last_document:
                    vals["date"] = date_last_document
        return vals

    def action_move_template_run(self):
        """Called by the button on the form view"""
        self.ensure_one()
        wiz = self.env["account.move.template.run"].create({"template_id": self.id})
        action = wiz.load_lines()
        return action

    @api.depends('invoice_date', 'company_id')
    def _compute_date(self):
        for move in self:
            if not move.invoice_date:
                if not move.date:
                    move.date = fields.Date.context_today(self)
                continue
            accounting_date = move.invoice_date
            if not move.is_sale_document(include_receipts=True):
                accounting_date = move._get_accounting_date(move.invoice_date, move._affect_tax_report())
            if accounting_date and accounting_date != move.date:
                move.date = accounting_date
                # _affect_tax_report may trigger premature recompute of line_ids.date
                self.env.add_to_compute(move.line_ids._fields['date'], move.line_ids)
                # might be protected because `_get_accounting_date` requires the `name`
                self.env.add_to_compute(self._fields['name'], move)

    @api.depends('move_type', 'origin_payment_id', 'statement_line_id')
    def _compute_journal_id(self):
        for move in self:
            move = self.env['account.move'].new({
                'move_type': move.move_type,
                'statement_line_id': move.statement_line_id.id,
            })
            if move.journal_id and move.journal_id.type not in move._get_valid_journal_types():
                move.journal_id = move._search_default_journal()

    @api.depends('company_id', 'invoice_filter_type_domain')
    def _compute_suitable_journal_ids(self):
        for m in self:
            move = self.env['account.move'].new({
                'invoice_filter_type_domain': m.invoice_filter_type_domain,
            })

            journal_type = move.invoice_filter_type_domain or 'general'
            company = self.env.company

            m.suitable_journal_ids = self.env['account.journal'].search([
                *move._check_company_domain(company),
                ('type', '=', journal_type),
            ])

    @api.depends('move_type')
    def _compute_invoice_filter_type_domain(self):
        for m in self:
            # Simulación de account.move
            fake_move = self.env['account.move'].new({
                'move_type': m.move_type,
            })

            if fake_move.is_sale_document(include_receipts=True):
                m.invoice_filter_type_domain = 'sale'
            elif fake_move.is_purchase_document(include_receipts=True):
                m.invoice_filter_type_domain = 'purchase'
            else:
                m.invoice_filter_type_domain = False