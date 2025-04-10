# wizard/account_move_template_run.py
from ast import literal_eval

from odoo import Command, _, fields, models
from odoo.exceptions import UserError, ValidationError


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
        domain="[('company_id', '=', company_id)]",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        domain="[('company_id', '=', company_id)]",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Partner",
        domain=["|", ("parent_id", "=", False), ("is_company", "=", True)],
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
    move_type = fields.Selection([
        ('entry', 'Journal Entry'),
        ('out_invoice', 'Customer Invoice'),
        ('out_refund', 'Customer Credit Note'),
        ('in_invoice', 'Vendor Bill'),
        ('in_refund', 'Vendor Credit Note'),
    ], default='entry', required=True)
    

    def load_lines(self):
        self.ensure_one()
        overwrite_vals = self._get_overwrite_vals()
        amtlro = self.env["account.move.template.line.run"]
        template = self.template_id
        tmpl_lines = template.line_ids

        for tmpl_line in tmpl_lines.filtered(lambda line: line.type == "input"):
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
            }
            amtlro.create(vals)

        journal = template.journal_id
        if not journal:
            domain = [('company_id', '=', self.company_id.id), ('type', '=', 'general')]
            journal = self.env['account.journal'].search(domain, limit=1)
            if not journal:
                raise UserError(_("No journal available for this template."))

        self.write({
            'journal_id': journal.id,
            "ref": template.ref,
        })

        if not self.line_ids:
            return self.generate_move()

        result = self.env["ir.actions.actions"]._for_xml_id(
            "account_move_template.account_move_template_run_action"
        )
        result.update({"res_id": self.id, "context": self.env.context})

        # Aplicar sobreescritura a las líneas visibles
        self._overwrite_line(overwrite_vals)

        # Limpiar 'amount' del contexto de sobreescritura para la próxima etapa
        for key in overwrite_vals.keys():
            overwrite_vals[key].pop("amount", None)

        result["context"] = dict(result.get("context", {}), overwrite=overwrite_vals)
        return result

    def generate_move(self):
        self.ensure_one()
        
        # Mapeo de secuencia → monto ingresado por el usuario
        sequence2amount = {
            wizard_line.sequence: wizard_line.amount
            for wizard_line in self.line_ids
        }

        company_cur = self.company_id.currency_id
        self.template_id.compute_lines(sequence2amount)

        move_vals = {
            "ref": self.ref,
            "journal_id": self.journal_id.id,
            "date": self.date,
            "company_id": self.company_id.id,
            "line_ids": [],
        }

        for line in self.template_id.line_ids:
            amount = sequence2amount.get(line.sequence, 0.0)
            if not company_cur.is_zero(amount):
                move_vals["line_ids"].append(
                    Command.create(self._prepare_move_line(line, amount))
                )

        move = self.env["account.move"].create(move_vals)

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
        return ["partner_id", "amount", "name", "date_maturity"]

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
        # First level keys must be L1, L2, ...
        keys = overwrite_vals.keys()
        if list(filter(lambda x: x[:1] != "L" or not x[1:].isdigit(), keys)):
            raise ValidationError(_("Keys must be line sequence i.e. L1, L2, ..."))
        # Second level keys must be a valid keys
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
                    'L1': {'partner_id': 1, 'amount': 10},
                    'L2': {'partner_id': 2, 'amount': 20},
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

    def _safe_vals(self, model, vals):
        obj = self.env[model]
        copy_vals = vals.copy()
        invalid_keys = list(
            set(list(vals.keys())) - set(list(dict(obj._fields).keys()))
        )
        for key in invalid_keys:
            copy_vals.pop(key)
        return copy_vals

    def _prepare_move_line(self, line, amount):
        date_maturity = False
        if hasattr(line, 'payment_term_id') and line.payment_term_id:
            pterm_list = line.payment_term_id.compute(value=1, date_ref=self.date)
            date_maturity = max(line[0] for line in pterm_list)
        debit = line.move_line_type == "dr"
        values = {
            "name": line.name,
            "account_id": line.account_id.id,
            "credit": not debit and amount or 0.0,
            "debit": debit and amount or 0.0,
            "partner_id": self.partner_id.id or line.partner_id.id,
            "date_maturity": date_maturity or self.date,
        }
        
        # Add optional fields if they exist
        if hasattr(line, 'tax_repartition_line_id') and line.tax_repartition_line_id:
            values["tax_repartition_line_id"] = line.tax_repartition_line_id.id
            
        if hasattr(line, 'analytic_distribution') and line.analytic_distribution:
            values["analytic_distribution"] = line.analytic_distribution
            
        if line.tax_ids:
            values["tax_ids"] = [Command.set(line.tax_ids.ids)]
            
            if hasattr(line, 'is_refund') and line.is_refund:
                tax_repartition = "refund_tax_id" if line.is_refund else "invoice_tax_id"
                atrl_ids = self.env["account.tax.repartition.line"].search(
                    [
                        (tax_repartition, "in", line.tax_ids.ids),
                        ("repartition_type", "=", "base"),
                    ]
                )
                if atrl_ids:
                    values["tax_tag_ids"] = [Command.set(atrl_ids.mapped("tag_ids").ids)]
                    
        # With overwrite options
        overwrite = self._context.get("overwrite", {})
        move_line_vals = overwrite.get(f"L{line.sequence}", {})
        values.update(move_line_vals)
        
        # Use optional account when amount is negative
        self._update_account_on_negative(line, values)
        return values

    def _update_account_on_negative(self, line, vals):
        if not hasattr(line, 'opt_account_id') or not line.opt_account_id:
            return
        for key in ["debit", "credit"]:
            if vals[key] < 0:
                ikey = "credit" if key == "debit" else "debit"
                vals["account_id"] = line.opt_account_id.id
                vals[ikey] = abs(vals[key])
                vals[key] = 0


# Modificaciones en la clase AccountMoveTemplateLineRun en wizard/account_move_template_run.py

class AccountMoveTemplateLineRun(models.TransientModel):
    _name = "account.move.template.line.run"
    _description = "Wizard Lines to generate move from template"
    _order = "sequence, id"  # Añadir orden para asegurar cálculos ordenados

    wizard_id = fields.Many2one(
        comodel_name="account.move.template.run",
        ondelete="cascade",
    )
    company_id = fields.Many2one(related="wizard_id.company_id")
    company_currency_id = fields.Many2one(
        related="wizard_id.company_id.currency_id", string="Company Currency"
    )
    name = fields.Char()  # Eliminar readonly para permitir edición
    sequence = fields.Integer(required=True)
    move_line_type = fields.Selection(
        [("cr", "Credit"), ("dr", "Debit")],
        required=True,
        readonly=True,
        string="Direction",
    )
    partner_id = fields.Many2one("res.partner", string="Partner")  # Eliminar readonly
    payment_term_id = fields.Many2one(
        "account.payment.term", string="Payment Terms"
    )
    account_id = fields.Many2one("account.account", required=True)  # Eliminar readonly
    tax_ids = fields.Many2many("account.tax", string="Taxes", readonly=True)
    tax_line_id = fields.Many2one(
        "account.tax", string="Originator Tax", ondelete="restrict", readonly=True
    )
    tax_repartition_line_id = fields.Many2one(
        "account.tax.repartition.line",
        string="Tax Repartition Line",
        readonly=True,
    )
    amount = fields.Monetary(required=True, currency_field="company_currency_id")
    note = fields.Char()
    is_refund = fields.Boolean(string="Is a refund?", readonly=True)
    analytic_distribution = fields.Json('Analytic')
    analytic_precision = fields.Integer(
        store=False,
        default=lambda self: self.env['decimal.precision'].precision_get("Percentage Analytic"),
    )
    # Nuevos campos para gestionar el cálculo
    template_type = fields.Selection(
        [
            ("input", "User input"),
            ("computed", "Computed"),
        ],
        string="Template Type",
        readonly=True,
    )
    python_code = fields.Text(string="Formula", readonly=True)