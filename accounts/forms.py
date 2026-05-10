from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import InviteCode

User = get_user_model()


class RegisterForm(forms.ModelForm):
    password = forms.CharField(
        label="密码",
        widget=forms.PasswordInput(attrs={"class": "w-full border rounded px-3 py-2"}),
        min_length=8,
    )
    password_confirm = forms.CharField(
        label="确认密码",
        widget=forms.PasswordInput(attrs={"class": "w-full border rounded px-3 py-2"}),
    )
    invite_code = forms.CharField(
        label="邀请码",
        max_length=64,
        widget=forms.TextInput(attrs={"class": "w-full border rounded px-3 py-2"}),
    )

    class Meta:
        model = User
        fields = ["username"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "w-full border rounded px-3 py-2"}),
        }
        labels = {"username": "用户名"}

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise ValidationError("该用户名已被使用")
        if len(username) < 3:
            raise ValidationError("用户名至少需要 3 个字符")
        return username

    def clean_invite_code(self):
        code_str = self.cleaned_data["invite_code"].strip()
        try:
            invite = InviteCode.objects.get(code=code_str)
        except InviteCode.DoesNotExist:
            raise ValidationError("邀请码无效")

        if invite.status == InviteCode.Status.USED:
            raise ValidationError("该邀请码已被使用")
        if invite.status == InviteCode.Status.DISABLED:
            raise ValidationError("该邀请码已停用")

        return invite

    def clean(self):
        data = super().clean()
        pwd = data.get("password")
        pwd2 = data.get("password_confirm")
        if pwd and pwd2 and pwd != pwd2:
            self.add_error("password_confirm", "两次输入的密码不一致")
        return data


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="用户名",
        widget=forms.TextInput(attrs={"class": "w-full border rounded px-3 py-2"}),
    )
    password = forms.CharField(
        label="密码",
        widget=forms.PasswordInput(attrs={"class": "w-full border rounded px-3 py-2"}),
    )
