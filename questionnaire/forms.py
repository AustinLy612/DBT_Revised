from django import forms
from django.core.exceptions import ValidationError

from .models import UserProfile


class ProfileForm(forms.ModelForm):
    HOBBY_CHOICES = [
        ("体育运动", "体育运动"),
        ("音乐", "音乐"),
        ("绘画/手工", "绘画/手工"),
        ("阅读", "阅读"),
        ("写作", "写作"),
        ("动漫/影视", "动漫/影视"),
        ("游戏", "游戏"),
        ("编程/科技", "编程/科技"),
        ("科学探索", "科学探索"),
        ("宠物", "宠物"),
        ("美食/烹饪", "美食/烹饪"),
        ("旅行/户外", "旅行/户外"),
        ("和朋友聊天", "和朋友聊天"),
        ("独处/安静活动", "独处/安静活动"),
        ("其他", "其他"),
    ]

    CONCERN_CHOICES = [
        ("学业压力", "学业压力"),
        ("考试焦虑", "考试焦虑"),
        ("注意力不集中", "注意力不集中"),
        ("拖延", "拖延"),
        ("和同学/朋友关系不好", "和同学/朋友关系不好"),
        ("感到孤独或被排斥", "感到孤独或被排斥"),
        ("家庭沟通困难", "家庭沟通困难"),
        ("和父母/监护人冲突", "和父母/监护人冲突"),
        ("情绪低落", "情绪低落"),
        ("容易生气或情绪失控", "容易生气或情绪失控"),
        ("经常担心或焦虑", "经常担心或焦虑"),
        ("不自信/自我评价低", "不自信/自我评价低"),
        ("睡眠困扰", "睡眠困扰"),
        ("外貌或身体形象焦虑", "外貌或身体形象焦虑"),
        ("手机/网络使用困扰", "手机/网络使用困扰"),
        ("升学或未来压力", "升学或未来压力"),
        ("和老师沟通困难", "和老师沟通困难"),
        ("被欺负或校园欺凌", "被欺负或校园欺凌"),
        ("不想回答", "不想回答"),
        ("其他", "其他"),
    ]

    hobby_tags = forms.MultipleChoiceField(
        label="爱好（最多选 5 项）",
        choices=HOBBY_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "hobby-checkbox"}),
        required=False,
    )
    concern_tags = forms.MultipleChoiceField(
        label="苦恼/困扰（最多选 5 项）",
        choices=CONCERN_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "concern-checkbox"}),
        required=False,
    )

    class Meta:
        model = UserProfile
        fields = [
            "gender",
            "age",
            "grade",
            "hobby_tags",
            "concern_tags",
            "other_hobby_text",
            "other_concern_text",
        ]
        widgets = {
            "gender": forms.RadioSelect(attrs={"class": "gender-radio"}),
            "age": forms.NumberInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "min": 10,
                "max": 25,
                "placeholder": "请输入你的年龄",
            }),
            "grade": forms.Select(attrs={"class": "w-full border rounded px-3 py-2"}),
            "other_hobby_text": forms.Textarea(attrs={
                "class": "w-full border rounded px-3 py-2",
                "rows": 2,
                "placeholder": '如果你选择了「其他」，请在此补充',
            }),
            "other_concern_text": forms.Textarea(attrs={
                "class": "w-full border rounded px-3 py-2",
                "rows": 2,
                "placeholder": '如果你选择了「其他」，请在此补充',
            }),
        }
        labels = {
            "gender": "性别",
            "age": "年龄",
            "grade": "年级",
            "other_hobby_text": "其他爱好补充",
            "other_concern_text": "其他困扰补充",
        }

    def clean_age(self):
        age = self.cleaned_data["age"]
        if age < 10 or age > 25:
            raise ValidationError("请输入合理的年龄（10-25 岁）")
        return age

    def clean_hobby_tags(self):
        tags = self.cleaned_data["hobby_tags"]
        if len(tags) > 5:
            raise ValidationError("最多选择 5 项爱好")
        return tags

    def clean_concern_tags(self):
        tags = self.cleaned_data["concern_tags"]
        if len(tags) > 5:
            raise ValidationError("最多选择 5 项困扰")
        return tags
