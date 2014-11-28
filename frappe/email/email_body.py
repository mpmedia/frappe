# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import frappe
from frappe import throw, _
from frappe.utils.pdf import get_pdf
from frappe.email.smtp import get_outgoing_email_account
from frappe.utils import get_url, scrub_urls
import email.utils
from markdown2 import markdown

def get_email(recipients, sender='', msg='', subject='[No Subject]',
	text_content = None, footer=None, print_html=None, formatted=None, attachments=None,
	content=None):
	"""send an html email as multipart with attachments and all"""
	content = content or msg
	emailobj = EMail(sender, recipients, subject)

	if not content.strip().startswith("<"):
		content = markdown(content)

	emailobj.set_html(content, text_content, footer=footer, print_html=print_html, formatted=formatted)

	if isinstance(attachments, dict):
		attachments = [attachments]

	for attach in (attachments or []):
		emailobj.add_attachment(**attach)

	return emailobj

class EMail:
	"""
	Wrapper on the email module. Email object represents emails to be sent to the client.
	Also provides a clean way to add binary `FileData` attachments
	Also sets all messages as multipart/alternative for cleaner reading in text-only clients
	"""
	def __init__(self, sender='', recipients=(), subject='', alternative=0, reply_to=None):
		from email.mime.multipart import MIMEMultipart
		from email import Charset
		Charset.add_charset('utf-8', Charset.QP, Charset.QP, 'utf-8')

		if isinstance(recipients, basestring):
			recipients = recipients.replace(';', ',').replace('\n', '')
			recipients = recipients.split(',')

		# remove null
		recipients = filter(None, (r.strip() for r in recipients))

		self.sender = sender
		self.reply_to = reply_to or sender
		self.recipients = recipients
		self.subject = subject

		self.msg_root = MIMEMultipart('mixed')
		self.msg_multipart = MIMEMultipart('alternative')
		self.msg_root.attach(self.msg_multipart)
		self.cc = []
		self.html_set = False

	def set_html(self, message, text_content = None, footer=None, print_html=None, formatted=None):
		"""Attach message in the html portion of multipart/alternative"""
		if not formatted:
			formatted = get_formatted_html(self.subject, message, footer, print_html)

		# this is the first html part of a multi-part message,
		# convert to text well
		if not self.html_set:
			if text_content:
				self.set_text(text_content)
			else:
				self.set_html_as_text(formatted)

		self.set_part_html(formatted)
		self.html_set = True

	def set_text(self, message):
		"""
			Attach message in the text portion of multipart/alternative
		"""
		from email.mime.text import MIMEText
		part = MIMEText(message, 'plain', 'utf-8')
		self.msg_multipart.attach(part)

	def set_part_html(self, message):
		from email.mime.text import MIMEText
		part = MIMEText(message, 'html', 'utf-8')
		self.msg_multipart.attach(part)

	def set_html_as_text(self, html):
		"""return html2text"""
		import HTMLParser
		from frappe.email.html2text import html2text
		try:
			self.set_text(html2text(html))
		except HTMLParser.HTMLParseError:
			pass

	def set_message(self, message, mime_type='text/html', as_attachment=0, filename='attachment.html'):
		"""Append the message with MIME content to the root node (as attachment)"""
		from email.mime.text import MIMEText

		maintype, subtype = mime_type.split('/')
		part = MIMEText(message, _subtype = subtype)

		if as_attachment:
			part.add_header('Content-Disposition', 'attachment', filename=filename)

		self.msg_root.attach(part)

	def attach_file(self, n):
		"""attach a file from the `FileData` table"""
		from frappe.utils.file_manager import get_file
		res = get_file(n)
		if not res:
			return

		self.add_attachment(res[0], res[1])

	def add_attachment(self, fname, fcontent, content_type=None):
		"""add attachment"""
		from email.mime.audio import MIMEAudio
		from email.mime.base import MIMEBase
		from email.mime.image import MIMEImage
		from email.mime.text import MIMEText

		import mimetypes
		if not content_type:
			content_type, encoding = mimetypes.guess_type(fname)

		if content_type is None:
			# No guess could be made, or the file is encoded (compressed), so
			# use a generic bag-of-bits type.
			content_type = 'application/octet-stream'

		maintype, subtype = content_type.split('/', 1)
		if maintype == 'text':
			# Note: we should handle calculating the charset
			if isinstance(fcontent, unicode):
				fcontent = fcontent.encode("utf-8")
			part = MIMEText(fcontent, _subtype=subtype, _charset="utf-8")
		elif maintype == 'image':
			part = MIMEImage(fcontent, _subtype=subtype)
		elif maintype == 'audio':
			part = MIMEAudio(fcontent, _subtype=subtype)
		else:
			part = MIMEBase(maintype, subtype)
			part.set_payload(fcontent)
			# Encode the payload using Base64
			from email import encoders
			encoders.encode_base64(part)

		# Set the filename parameter
		if fname:
			part.add_header(b'Content-Disposition',
				("attachment; filename=\"%s\"" % fname).encode('utf-8'))

		self.msg_root.attach(part)

	def add_pdf_attachment(self, name, html, options=None):
		self.add_attachment(name, get_pdf(html, options), 'application/octet-stream')

	def validate(self):
		"""validate the email ids"""
		from frappe.utils import validate_email_add
		def _validate(email):
			"""validate an email field"""
			if email and not validate_email_add(email):
				throw(_("{0} is not a valid email id").format(email), frappe.InvalidEmailAddressError)
			return email

		if not self.sender:
			self.sender = get_outgoing_email_account().email_id

		self.sender = _validate(self.sender)
		self.reply_to = _validate(self.reply_to)

		for e in self.recipients + (self.cc or []):
			_validate(e.strip())

	def set_message_id(self, message_id):
		self.msg_root["Message-Id"] = "<{0}@{1}>".format(message_id, frappe.local.site)

	def make(self):
		"""build into msg_root"""
		self.msg_root['Subject'] = self.subject.encode("utf-8")
		self.msg_root['From'] = self.sender.encode("utf-8")
		self.msg_root['To'] = ', '.join([r.strip() for r in self.recipients]).encode("utf-8")
		self.msg_root['Date'] = email.utils.formatdate()
		if not self.reply_to:
			self.reply_to = self.sender
		self.msg_root['Reply-To'] = self.reply_to.encode("utf-8")
		if self.cc:
			self.msg_root['CC'] = ', '.join([r.strip() for r in self.cc]).encode("utf-8")

		# add frappe site header
		self.msg_root.add_header(b'X-Frappe-Site', get_url().encode('utf-8'))

	def as_string(self):
		"""validate, build message and convert to string"""
		self.validate()
		self.make()
		return self.msg_root.as_string()

def get_formatted_html(subject, message, footer=None, print_html=None):
	# imported here to avoid cyclic import

	message = scrub_urls(message)
	rendered_email = frappe.get_template("templates/emails/standard.html").render({
		"content": message,
		"footer": get_footer(footer),
		"title": subject,
		"print_html": print_html,
		"subject": subject
	})

	return rendered_email

def get_footer(footer=None):
	"""append a footer (signature)"""
	footer = footer or ""

	email_account = get_outgoing_email_account(False)

	if email_account and email_account.add_signature and email_account.signature:
		footer += email_account.signature

	if email_account and email_account.footer:
		footer += email_account.footer
	else:
		for default_mail_footer in frappe.get_hooks("default_mail_footer"):
			footer += default_mail_footer

	footer += "<!--unsubscribe link here-->"

	return footer