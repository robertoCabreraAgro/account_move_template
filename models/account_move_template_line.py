from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

class AccountMoveTemplateLine(models.Model):
    _name = "account.move.template.line"
    _description = "Journal Item Template"
    _order = "sequence, id"
    _check_company_auto = False  # Desactivamos el check automático para manejar multicompañía

    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        related="template_id.company_id",
        store=True,
    )
    # Campo relacionado con la compañía destino
    target_company_id = fields.Many2one(
        comodel_name="res.company",
        string="Target Company",
        related="template_id.target_company_id",
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
    # Reemplazamos el campo account_id por un Many2one que se llena con el resultado de la búsqueda
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        check_company=False,
    )
    # Nuevo campo para código de cuenta
    account_code = fields.Char(
        string="Account Code",
        help="Code of the account to use. This will search for an account with this code in the target company."
    )
    # Campo para almacenar el nombre de la cuenta para mejor UI
    account_name = fields.Char(
        string="Account Name",
        help="Name of the account (informational)"
    )
    # Campos similares para la cuenta opcional
    opt_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account if Negative",
        check_company=False,
        help="When amount is negative, use this account instead",
    )
    opt_account_code = fields.Char(
        string="Opt. Account Code",
        help="Code of the optional account to use for negative amounts."
    )
    opt_account_name = fields.Char(
        string="Opt. Account Name",
        help="Name of the optional account (informational)"
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        check_company=False,
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
        check_company=False,
    )
    move_line_type = fields.Selection(
        selection=[("cr", "Credit"), ("dr", "Debit")],
        string="Direction",
        required=True,
    )
    sequence = fields.Integer(required=True)
    account_prefix = fields.Char(
        string='Accounts Prefix',
        help="When creating a new journal item an account having this prefix"
             "will be looked for",
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
                # Usar la compañía destino si está definida
                target_company = line.target_company_id or line.company_id
                
                # Obtener las cuentas del producto para la compañía correcta
                product = line.product_id.with_company(target_company)
                accounts = product.product_tmpl_id.get_product_accounts(fiscal_pos=None)
                
                if accounts.get('expense') and line.move_line_type == 'dr':
                    line.account_id = accounts['expense'].id
                    line.account_code = accounts['expense'].code
                    line.account_name = accounts['expense'].name
                elif accounts.get('income') and line.move_line_type == 'cr':
                    line.account_id = accounts['income'].id
                    line.account_code = accounts['income'].code
                    line.account_name = accounts['income'].name
                    
    @api.onchange("account_code", "target_company_id")
    def _onchange_account_code(self):
        """Busca la cuenta que corresponde al código ingresado"""
        for line in self:
            if not line.account_code or not line.target_company_id:
                continue
                
            # Buscar cuenta por código sin filtro de compañía
            accounts = self.env['account.account'].sudo().search([
                ('code', '=', line.account_code)
            ])
            
            # Filtrar manualmente verificando que la compañía destino esté en company_ids
            account = False
            for acc in accounts:
                # Para compatibilidad con diferentes versiones, verificamos si existe company_id o company_ids
                if hasattr(acc, 'company_id'):
                    if acc.company_id.id == line.target_company_id.id:
                        account = acc
                        break
                elif hasattr(acc, 'company_ids'):
                    if line.target_company_id.id in acc.company_ids.ids:
                        account = acc
                        break
            
            if account:
                line.account_id = account.id
                line.account_name = account.name
                _logger.info("Encontrada cuenta para código %s: %s (ID: %s) en compañía %s", 
                        line.account_code, account.name, account.id, line.target_company_id.name)
            else:
                # Si no encuentra exacto, buscar por códigos que empiecen igual
                accounts = self.env['account.account'].sudo().search([
                    ('code', '=like', line.account_code + '%')
                ])
                
                # Filtrar manualmente
                for acc in accounts:
                    # Para compatibilidad con diferentes versiones
                    if hasattr(acc, 'company_id'):
                        if acc.company_id.id == line.target_company_id.id:
                            account = acc
                            break
                    elif hasattr(acc, 'company_ids'):
                        if line.target_company_id.id in acc.company_ids.ids:
                            account = acc
                            break
                
                if account:
                    line.account_id = account.id
                    line.account_name = account.name
                    line.account_code = account.code  # Actualizar al código completo
                    _logger.info("Encontrada cuenta similar para código %s: %s (ID: %s)", 
                            line.account_code, account.name, account.id)
                else:
                    line.account_id = False
                    line.account_name = False
                    _logger.warning("No se encontró cuenta con código %s en la compañía %s", 
                                line.account_code, line.target_company_id.name)

    @api.onchange("opt_account_code", "target_company_id")
    def _onchange_opt_account_code(self):
        """Busca la cuenta opcional que corresponde al código ingresado"""
        for line in self:
            if not line.opt_account_code or not line.target_company_id:
                continue
                
            # Buscar cuenta por código sin filtro de compañía
            accounts = self.env['account.account'].sudo().search([
                ('code', '=', line.opt_account_code)
            ])
            
            # Filtrar manualmente verificando que la compañía destino esté en company_ids
            account = False
            for acc in accounts:
                # Para compatibilidad con diferentes versiones, verificamos si existe company_id o company_ids
                if hasattr(acc, 'company_id'):
                    if acc.company_id.id == line.target_company_id.id:
                        account = acc
                        break
                elif hasattr(acc, 'company_ids'):
                    if line.target_company_id.id in acc.company_ids.ids:
                        account = acc
                        break
            
            if account:
                line.opt_account_id = account.id
                line.opt_account_name = account.name
                _logger.info("Encontrada cuenta opcional para código %s: %s (ID: %s)", 
                        line.opt_account_code, account.name, account.id)
            else:
                # Si no encuentra exacto, buscar por códigos que empiecen igual
                accounts = self.env['account.account'].sudo().search([
                    ('code', '=like', line.opt_account_code + '%')
                ])
                
                # Filtrar manualmente
                for acc in accounts:
                    # Para compatibilidad con diferentes versiones
                    if hasattr(acc, 'company_id'):
                        if acc.company_id.id == line.target_company_id.id:
                            account = acc
                            break
                    elif hasattr(acc, 'company_ids'):
                        if line.target_company_id.id in acc.company_ids.ids:
                            account = acc
                            break
                
                if account:
                    line.opt_account_id = account.id
                    line.opt_account_name = account.name
                    line.opt_account_code = account.code  # Actualizar al código completo
                    _logger.info("Encontrada cuenta opcional similar para código %s: %s (ID: %s)", 
                            line.opt_account_code, account.name, account.id)
                else:
                    line.opt_account_id = False
                    line.opt_account_name = False
                    _logger.warning("No se encontró cuenta opcional con código %s en la compañía %s", 
                                line.opt_account_code, line.target_company_id.name)
                            
    @api.onchange("account_id")
    def _onchange_account_id(self):
        """Actualiza el código y nombre de cuenta cuando cambia account_id"""
        for line in self:
            if line.account_id:
                line.account_code = line.account_id.code
                line.account_name = line.account_id.name
                
    @api.onchange("opt_account_id")
    def _onchange_opt_account_id(self):
        """Actualiza el código y nombre de cuenta opcional cuando cambia opt_account_id"""
        for line in self:
            if line.opt_account_id:
                line.opt_account_code = line.opt_account_id.code
                line.opt_account_name = line.opt_account_id.name