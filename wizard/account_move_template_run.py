# wizard/account_move_template_run.py
from ast import literal_eval
import logging

from odoo import Command, _, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

class AccountMoveTemplateRun(models.TransientModel):
    _name = "account.move.template.run"
    _description = "Wizard to generate move from template"

    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    template_id = fields.Many2one(
        comodel_name="account.move.template",
        required=True,
        domain="['|', ('company_id', '=', company_id), ('target_company_id', '=', company_id)]",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        domain="[('company_id', '=', company_id)]",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain="['|', ('parent_id', '=', False), ('is_company', '=', True)]",
    )
    date = fields.Date(
        required=True,
        default=fields.Date.context_today,
    )
    ref = fields.Char(string="Reference")
    overwrite = fields.Text(
        help="""
             Valid dictionary to overwrite template lines:
             {'L1': {'partner_id': 1, 'amount': 100, 'name': 'some label'},
             'L2': {'partner_id': 2, 'amount': 200, 'name': 'some label 2'}, }
             """
    )
    line_ids = fields.One2many(
        comodel_name="account.move.template.line.run",
        inverse_name="wizard_id",
        string="Lines",
    )
    move_type = fields.Selection(
        selection=[
            ('entry', 'Journal Entry'),
            ('out_invoice', 'Customer Invoice'),
            ('out_refund', 'Customer Credit Note'),
            ('in_invoice', 'Vendor Bill'),
            ('in_refund', 'Vendor Credit Note'),
        ],
        default='entry',
        required=True,
    )
    price_unit = fields.Float(
        string='Unit Price',
        default=0.0,
    )
   
    def load_lines(self):
        self.ensure_one()
        overwrite_vals = self._get_overwrite_vals()
        amtlro = self.env["account.move.template.line.run"]
        template_run = self
        template = self.template_id
        tmpl_lines = template.line_ids
        
        self.line_ids.unlink()

        # Asegurar que estamos usando la compañía correcta
        # Si el template tiene target_company_id, usamos esa
        target_company = template.target_company_id if hasattr(template, 'target_company_id') and template.target_company_id else template.company_id
        if target_company and self.company_id != target_company:
            self.company_id = target_company.id
            
            # Asegurar que el diario es válido para la compañía
            if self.journal_id and self.journal_id.company_id != target_company:
                if template.journal_id and template.journal_id.company_id == target_company:
                    self.journal_id = template.journal_id.id
                else:
                    domain = [('company_id', '=', target_company.id), ('type', '=', 'general')]
                    journal = self.env['account.journal'].search(domain, limit=1)
                    if journal:
                        self.journal_id = journal.id
                    else:
                        self.journal_id = False

        for tmpl_line in tmpl_lines:
            vals = {
                "wizard_id": self.id,
                "sequence": tmpl_line.sequence,
                "name": tmpl_line.name,
                "amount": 0.0,
                "account_id": tmpl_line.account_id.id,
                "partner_id": tmpl_line.partner_id.id or False,
                "move_line_type": tmpl_line.move_line_type,
                "tax_ids": [(6, 0, tmpl_line.tax_ids.ids)],
                "note": tmpl_line.note,
                "payment_term_id": tmpl_line.payment_term_id.id if hasattr(tmpl_line, 'payment_term_id') else False,
                "template_type": tmpl_line.type,
                "python_code": tmpl_line.python_code if tmpl_line.type == 'computed' else False,
                "price_unit": self.price_unit if hasattr(self, 'price_unit') else 0.0,
            }
            
            if hasattr(tmpl_line, 'analytic_distribution') and tmpl_line.analytic_distribution:
                vals["analytic_distribution"] = tmpl_line.analytic_distribution
                
            amtlro.create(vals)

        journal = template.journal_id
        if not journal:
            domain = [('company_id', '=', self.company_id.id), ('type', '=', 'general')]
            journal = self.env['account.journal'].search(domain, limit=1)
            if not journal:
                raise UserError(_("No journal available for this template."))

        self.write({
            'journal_id': journal.id,
            "ref": template_run.ref,
            "partner_id": template.partner_id.id or self.partner_id.id,
            "date": template.date or self.date,
        })

        if not self.line_ids:
            return self.generate_move()

        self._overwrite_line(overwrite_vals)
        self._compute_line_values()

        result = self.env["ir.actions.actions"]._for_xml_id(
            "account_move_template.account_move_template_run_action"
        )
        result.update({"res_id": self.id, "context": self.env.context})

        for key in overwrite_vals.keys():
            overwrite_vals[key].pop("amount", None)

        result["context"] = dict(result.get("context", {}), overwrite=overwrite_vals)
        return result

    def _compute_line_values(self):
        self.ensure_one()
        
        sequence2amount = {}
        input_lines = self.line_ids.filtered(lambda l: l.template_type == 'input')
        for line in input_lines:
            sequence2amount[line.sequence] = line.amount
            
        computed_lines = self.line_ids.filtered(lambda l: l.template_type == 'computed')
        
        if not computed_lines:
            return
            
        for line in computed_lines.sorted(lambda l: l.sequence):
            try:
                sequence = line.sequence
                eval_context = {f"L{seq}": sequence2amount.get(seq, 0.0) 
                            for seq in sequence2amount.keys() if seq < sequence}
                
                if line.python_code and eval_context:
                    amount = safe_eval(line.python_code, eval_context)
                    line.amount = amount
                    sequence2amount[sequence] = amount
                    
                    if hasattr(self, 'price_unit') and self.price_unit:
                        line.price_unit = self.price_unit
            except Exception as e:
                _logger.error("Error al calcular línea %s: %s", line.sequence, str(e))

    def generate_move(self):
        self.ensure_one()
        
        self._compute_line_values()
        
        sequence2amount = {
            wizard_line.sequence: wizard_line.amount
            for wizard_line in self.line_ids
        }

        company_cur = self.company_id.currency_id

        # Verificar que estamos usando la compañía correcta para el asiento
        template = self.template_id
        target_company = template.target_company_id if hasattr(template, 'target_company_id') and template.target_company_id else self.company_id

        move_vals = {
            "ref": self.ref,
            "journal_id": self.journal_id.id,
            "date": self.date,
            "company_id": target_company.id,
            "move_type": self.move_type,
            "line_ids": [],
        }
        
        if self.partner_id:
            move_vals["partner_id"] = self.partner_id.id

        for line in self.line_ids:
            amount = line.amount
            if not company_cur.is_zero(amount) and amount:
                move_vals["line_ids"].append(
                    Command.create(self._prepare_move_line(line, amount))
                )

        move = self.env["account.move"].create(move_vals)
        
        if hasattr(self, 'price_unit') and self.price_unit:
            for move_line in move.line_ids:
                move_line.price_unit = self.price_unit

        result = self.env["ir.actions.actions"]._for_xml_id(
            "account.action_move_journal_line"
        )
        result.update({
            "name": _("Entry from template %s") % self.template_id.name,
            "res_id": move.id,
            "views": False,
            "view_id": False,
            "view_mode": "form,list",
            "context": self.env.context,
        })
        return result

    def _get_valid_keys(self):
        return ["partner_id", "amount", "name", "date_maturity", "price_unit"]

    def _get_overwrite_vals(self):
        self.ensure_one()
        valid_keys = self._get_valid_keys()
        overwrite_vals = self.overwrite or "{}"
        try:
            overwrite_vals = literal_eval(overwrite_vals)
            assert isinstance(overwrite_vals, dict)
        except (SyntaxError, ValueError, AssertionError) as err:
            raise ValidationError(
                _("Overwrite value must be a valid python dict")
            ) from err
        keys = overwrite_vals.keys()
        if list(filter(lambda x: x[:1] != "L" or not x[1:].isdigit(), keys)):
            raise ValidationError(_("Keys must be line sequence i.e. L1, L2, ..."))
        try:
            if dict(
                filter(lambda x: set(overwrite_vals[x].keys()) - set(valid_keys), keys)
            ):
                raise ValidationError(
                    _("Valid fields to overwrite are %s") % valid_keys
                )
        except ValidationError as e:
            raise e
        except Exception as e:
            msg = """
                valid_dict = {
                    'L1': {'partner_id': 1, 'amount': 10, 'price_unit': 10},
                    'L2': {'partner_id': 2, 'amount': 20, 'price_unit': 20},
            }
            """
            raise ValidationError(
                _(
                    "Invalid dictionary: %(exception)s\n%(msg)s",
                    exception=e,
                    msg=msg,
                )
            ) from e
        return overwrite_vals

    def _overwrite_line(self, overwrite_vals):
        self.ensure_one()
        for line in self.line_ids:
            vals = overwrite_vals.get(f"L{line.sequence}", {})
            safe_vals = self._safe_vals(line._name, vals)
            line.write(safe_vals)

    def _prepare_wizard_line(self, tmpl_line):
        vals = {
            "wizard_id": self.id,
            "sequence": tmpl_line.sequence,
            "name": tmpl_line.name,
            "amount": 0.0,
            "account_id": tmpl_line.account_id.id,
            "partner_id": tmpl_line.partner_id.id or False,
            "move_line_type": tmpl_line.move_line_type,
            "tax_ids": [Command.set(tmpl_line.tax_ids.ids)],
            "note": tmpl_line.note,
            "price_unit": self.price_unit if hasattr(self, 'price_unit') and self.price_unit else 0.0,
        }
        return vals

    def _prepare_move(self):
        move_vals = {
            "ref": self.ref,
            "journal_id": self.journal_id.id,
            "date": self.date,
            "company_id": self.company_id.id,
            "line_ids": [],
        }
        return move_vals

    def _safe_vals(self, model, vals):
        obj = self.env[model]
        copy_vals = vals.copy()
        invalid_keys = list(
            set(list(vals.keys())) - set(list(dict(obj._fields).keys()))
        )
        for key in invalid_keys:
            copy_vals.pop(key)
        return copy_vals
    
    def _update_account_on_negative(self, line, vals):
        if not hasattr(line, 'opt_account_id') or not line.opt_account_id:
            return
        for key in ["debit", "credit"]:
            if vals[key] < 0:
                ikey = "credit" if key == "debit" else "debit"
                vals["account_id"] = line.opt_account_id.id
                vals[ikey] = abs(vals[key])
                vals[key] = 0

    def _prepare_move_line(self, line, amount):
        date_maturity = False
        if hasattr(line, 'payment_term_id') and line.payment_term_id:
            pterm_list = line.payment_term_id.compute(value=1, date_ref=self.date)
            date_maturity = max(p[0] for p in pterm_list)

        debit = line.move_line_type == "dr"
        values = {
            "name": line.name,
            "account_id": line.account_id.id,
            "credit": 0.0 if debit else abs(amount),
            "debit": abs(amount) if debit else 0.0,
            "partner_id": line.partner_id.id or self.partner_id.id,
            "date_maturity": date_maturity or self.date,
        }
        
        if hasattr(line, 'price_unit') and line.price_unit:
            values["price_unit"] = line.price_unit
        elif hasattr(self, 'price_unit') and self.price_unit:
            values["price_unit"] = self.price_unit
        
        if line.tax_ids:
            values["tax_ids"] = [Command.set(line.tax_ids.ids)]

        if getattr(line, 'is_refund', False):
            tax_repartition = "refund_tax_id" if line.is_refund else "invoice_tax_id"
            atrl_ids = self.env["account.tax.repartition.line"].search(
                [
                    (tax_repartition, "in", line.tax_ids.ids),
                    ("repartition_type", "=", "base"),
                ]
            )
            if atrl_ids:
                values["tax_tag_ids"] = [Command.set(atrl_ids.mapped("tag_ids").ids)]

        if getattr(line, 'tax_repartition_line_id', False):
            values["tax_repartition_line_id"] = line.tax_repartition_line_id.id
            values["tax_tag_ids"] = [Command.set(line.tax_repartition_line_id.tag_ids.ids)]

        if getattr(line, 'analytic_distribution', False):
            values["analytic_distribution"] = line.analytic_distribution

        overwrite = self._context.get("overwrite", {})
        move_line_vals = overwrite.get(f"L{line.sequence}", {})
        values.update(move_line_vals)

        self._update_account_on_negative(line, values)
        return values

class AccountMoveTemplateLineRun(models.TransientModel):
    _name = "account.move.template.line.run"
    _description = "Wizard Lines to generate move from template"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        comodel_name="account.move.template.run",
        ondelete="cascade",
    )
    company_id = fields.Many2one(related="wizard_id.company_id")
    company_currency_id = fields.Many2one(
        related="wizard_id.company_id.currency_id", 
        string="Company Currency"
    )
    name = fields.Char()
    sequence = fields.Integer(required=True)
    move_line_type = fields.Selection(
        selection=[("cr", "Credit"), ("dr", "Debit")],
        required=True,
        readonly=True,
        string="Direction",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner", 
        string="Partner"
    )
    payment_term_id = fields.Many2one(
        comodel_name="account.payment.term", 
        string="Payment Terms"
    )
    account_id = fields.Many2one(
        comodel_name="account.account", 
        required=True
    )
    tax_ids = fields.Many2many(
        comodel_name="account.tax", 
        string="Taxes", 
        readonly=True
    )
    tax_line_id = fields.Many2one(
        comodel_name="account.tax", 
        string="Originator Tax", 
        ondelete="restrict", 
        readonly=True
    )
    tax_repartition_line_id = fields.Many2one(
        comodel_name="account.tax.repartition.line",
        string="Tax Repartition Line",
        readonly=True,
    )
    amount = fields.Monetary(
        required=True, 
        currency_field="company_currency_id"
    )
    note = fields.Char()
    is_refund = fields.Boolean(
        string="Is a refund?", 
        readonly=True
    )
    analytic_distribution = fields.Json('Analytic')
    analytic_precision = fields.Integer(
        store=False,
        default=lambda self: self.env['decimal.precision'].precision_get("Percentage Analytic"),
    )
    template_type = fields.Selection(
        selection=[
            ("input", "User input"),
            ("computed", "Computed"),
        ],
        string="Template Type",
        readonly=True,
    )
    python_code = fields.Text(
        string="Formula", 
        readonly=True
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
    )
    price_unit = fields.Float(
        string='Unit Price',
        default=0.0,
    )