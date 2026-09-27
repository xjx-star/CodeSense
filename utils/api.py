"""
API功能支持模块
提供JSON响应和API结构
"""
from flask import jsonify
from dataclasses import dataclass, asdict
from typing import Any, Optional, Union
from enum import Enum
import json

from utils.access import authoritative_class_name


class ResponseStatus(Enum):
    """响应状态枚举"""
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"  # 部分成功


@dataclass
class APIResponse:
    """标准化 API 响应数据结构"""
    status: ResponseStatus
    message: str
    data: Optional[Any] = None
    error_code: Optional[str] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "success": self.status == ResponseStatus.SUCCESS,
            "message": self.message,
        }
        if self.data is not None:
            result["data"] = self.data
        if self.error_code:
            result["error_code"] = self.error_code
        return result

    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_flask_response(self, status_code: int = 200):
        """转换为 Flask 响应"""
        return jsonify(self.to_dict()), status_code


class APIResponseBuilder:
    """
    API 响应构建器

    提供链式调用方式构建标准化响应

    使用示例:
        return APIResponseBuilder.success(
            message="获取成功",
            data={'items': [...]}
        ).to_flask_response()

        return APIResponseBuilder.error(
            message="权限不足",
            error_code="FORBIDDEN",
            status_code=403
        )
    """

    @staticmethod
    def success(
        message: str = "操作成功",
        data: Any = None
    ) -> APIResponse:
        """
        创建成功响应

        Args:
            message: 成功消息
            data: 响应数据

        Returns:
            APIResponse 对象
        """
        return APIResponse(
            status=ResponseStatus.SUCCESS,
            message=message,
            data=data
        )

    @staticmethod
    def error(
        message: str,
        error_code: Optional[str] = None,
        status_code: int = 400
    ) -> tuple:
        """
        创建错误响应

        Args:
            message: 错误消息
            error_code: 错误代码
            status_code: HTTP 状态码

        Returns:
            Flask 响应元组 (response, status_code)
        """
        return APIResponse(
            status=ResponseStatus.ERROR,
            message=message,
            error_code=error_code
        ).to_flask_response(status_code)

    @staticmethod
    def partial(
        message: str = "部分成功",
        data: Any = None
    ) -> APIResponse:
        """
        创建部分成功响应

        Args:
            message: 提示消息
            data: 部分数据

        Returns:
            APIResponse 对象
        """
        return APIResponse(
            status=ResponseStatus.PARTIAL,
            message=message,
            data=data
        )

    @staticmethod
    def paginated(
        items: list,
        page: int,
        per_page: int,
        total: int
    ) -> APIResponse:
        """
        创建分页响应

        Args:
            items: 当前页数据
            page: 当前页码
            per_page: 每页数量
            total: 总数

        Returns:
            APIResponse 对象
        """
        return APIResponse(
            status=ResponseStatus.SUCCESS,
            message="获取成功",
            data={
                'items': items,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page if per_page > 0 else 0
                }
            }
        )


# ============ 遗留函数（保持向后兼容）============

def api_response(success=True, message="", data=None, code=200):
    """
    创建标准化的API响应（遗留函数，建议使用 APIResponseBuilder）

    参数:
        success: 操作是否成功
        message: 响应消息
        data: 响应数据
        code: HTTP状态码

    返回:
        JSON响应对象及状态码
    """
    response = {
        "success": success,
        "message": message,
        "data": data or {}
    }

    # 确保所有数据可JSON序列化
    try:
        # 尝试预序列化以捕获任何问题
        json.dumps(response)
    except (TypeError, ValueError, OverflowError) as e:
        print(f"API响应序列化错误: {e}")
        # 这里可能需要清理无法序列化的数据结构
        if data and "answer" in data:
            print(f"尝试清理answer字段，原长度: {len(data['answer'] if data['answer'] else 'None')}")
            # 确保答案为字符串并移除非ASCII字符
            data["answer"] = str(data["answer"]).encode('ascii', 'ignore').decode('ascii')
            print(f"清理后answer长度: {len(data['answer'])}")
            response["data"] = data
        elif data and "guidance" in data:
            print(f"尝试清理guidance字段，原长度: {len(data['guidance'] if data['guidance'] else 'None')}")
            # 确保指导为字符串并移除非ASCII字符
            data["guidance"] = str(data["guidance"]).encode('ascii', 'ignore').decode('ascii')
            print(f"清理后guidance长度: {len(data['guidance'])}")
            response["data"] = data

    return jsonify(response), code


def error_response(message, code=400):
    """
    创建错误响应（遗留函数，建议使用 APIResponseBuilder）

    参数:
        message: 错误消息
        code: HTTP状态码

    返回:
        JSON错误响应对象及状态码
    """
    return api_response(success=False, message=message, code=code)


def user_to_dict(user):
    """将用户对象转换为字典"""
    return {
        'student_id': user.student_id,
        'username': user.username,
        'full_name': user.full_name,
        'class_name': authoritative_class_name(user),
        'usertype': user.usertype,
        'submit_count': user.submit_count,
        'user_ascore': user.user_ascore
    }


def assignment_to_dict(assignment):
    """将作业对象转换为字典"""
    return {
        'id': assignment.id,
        'title': assignment.title,
        'description': assignment.description,
        'average_score': assignment.average_score,
        'count': assignment.count
    }


def submission_to_dict(submission):
    """将提交记录对象转换为字典"""
    return {
        'id': submission.id,
        'student_id': submission.student_id,
        'assignment_id': submission.assignment_id,
        'score': submission.score,
        'language': submission.language,
        'status': submission.status,
        'feedback': submission.feedback,
        'ai_feedback': submission.ai_feedback,
        'submitted_at': submission.submitted_at.isoformat() if submission.submitted_at else None
    }
