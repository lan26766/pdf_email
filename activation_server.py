

"""
PDF Fusion Pro - 激活服务器
主服务器文件 - 支持 Gumroad Webhook
"""

import os
import json
import base64
import hashlib
import logging
import smtplib
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from urllib.parse import parse_qs, unquote

from flask import Flask, request, jsonify
from flask_cors import CORS
from cryptography.fernet import Fernet

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化Flask应用
app = Flask(__name__)
CORS(app)

# 配置类
class Config:
    """应用配置"""
    
    # 从环境变量读取
    ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')
    ADMIN_API_KEY = os.getenv('ADMIN_API_KEY', '')
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    
    # 邮件配置（可选）
    SMTP_HOST = os.getenv('SMTP_HOST', '')
    SMTP_PORT = os.getenv('SMTP_PORT', '587')
    SMTP_USER = os.getenv('SMTP_USER', '')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
    
    # Gumroad配置（可选）
    GUMROAD_WEBHOOK_SECRET = os.getenv('GUMROAD_WEBHOOK_SECRET', '')
    
    @classmethod
    def validate(cls):
        """验证必要配置"""
        required = ['ENCRYPTION_KEY', 'ADMIN_API_KEY']
        missing = []
        
        for var in required:
            if not getattr(cls, var):
                missing.append(var)
        
        if missing:
            logger.error(f"❌ 缺少必要配置: {', '.join(missing)}")
            return False
        
        if not cls.DATABASE_URL:
            logger.warning("⚠️  未配置 DATABASE_URL，将使用本地文件存储")
        
        logger.info("✅ 配置验证通过")
        return True

# 初始化配置
config = Config()

def init_professional_components():
    """初始化专业组件"""
    try:
        # 初始化激活码生成器
        encryption_key = config.ENCRYPTION_KEY
        if not encryption_key:
            # 生成一个固定的开发密钥（生产环境必须使用安全的随机密钥）
            logger.warning("⚠️  ENCRYPTION_KEY 未配置，使用开发密钥")
            encryption_key = base64.urlsafe_b64encode(b'dev-key-32-bytes-for-testing-only!!')
        
        # 确保密钥是字符串
        if isinstance(encryption_key, bytes):
            encryption_key = encryption_key.decode('utf-8')
        
        cipher = Fernet(encryption_key)
        logger.info("✅ 加密组件初始化完成")
        
        # 初始化邮件发送器配置
        smtp_configured = all([
            config.SMTP_HOST,
            config.SMTP_USER,
            config.SMTP_PASSWORD
        ])
        
        if smtp_configured:
            logger.info(f"✅ 邮件服务已配置: {config.SMTP_USER}")
        else:
            logger.warning("⚠️  邮件服务未完全配置，将无法发送激活邮件")
        
        return cipher, smtp_configured
        
    except Exception as e:
        logger.error(f"❌ 专业组件初始化失败: {e}")
        return None, False

# 初始化专业组件
cipher, smtp_configured = init_professional_components()

def safe_init_database():
    """
    安全地初始化数据库
    如果失败，会降级到文件存储
    """
    if not config.DATABASE_URL:
        logger.info("💾 使用本地文件存储（未配置数据库）")
        return False
    
    try:
        # 尝试导入数据库模块
        from database.init_db import init_database
        
        logger.info("🔗 正在连接数据库...")
        success = init_database(config.DATABASE_URL)
        
        if success:
            logger.info("✅ 数据库初始化成功")
            return True
        else:
            logger.warning("⚠️  数据库初始化失败，降级到文件存储")
            return False
            
    except ImportError as e:
        logger.warning(f"⚠️  无法导入数据库模块: {e}")
        logger.warning("💾 降级到本地文件存储")
        return False
    except Exception as e:
        logger.error(f"❌ 数据库初始化异常: {e}")
        logger.warning("💾 降级到本地文件存储")
        return False

# ==================== 工具函数 ====================

def require_api_key(f):
    """API密钥验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != config.ADMIN_API_KEY:
            logger.warning(f"未授权访问尝试: {request.remote_addr}")
            return jsonify({"error": "未授权"}), 401
        return f(*args, **kwargs)
    return decorated_function

def parse_form_data(data):
    """解析 form-urlencoded 数据"""
    try:
        # 解析查询字符串
        parsed = parse_qs(data, keep_blank_values=True)
        
        # 将列表值转换为单个值，并解码 URL 编码
        result = {}
        for key, value in parsed.items():
            if isinstance(value, list):
                if len(value) == 1:
                    # 解码 URL 编码
                    result[key] = unquote(value[0])
                else:
                    result[key] = [unquote(v) for v in value]
            else:
                result[key] = unquote(value)
        
        return result
    except Exception as e:
        logger.error(f"解析 form-data 失败: {e}")
        return {}

def generate_professional_activation_code(email, product_type="personal", 
                                         purchase_id="", product_name=""):
    """生成专业的激活码（使用Fernet加密）"""
    try:
        if not cipher:
            logger.warning("⚠️  加密组件未初始化，降级到简单激活码")
            return generate_simple_activation_code(email, product_type)
        
        # 根据产品类型设置参数
        days_valid = 365
        max_devices = 3
        
        if product_type == 'business':
            days_valid = 365 * 2
            max_devices = 10
        elif product_type == 'enterprise':
            days_valid = 365 * 3
            max_devices = 99
        elif product_type == 'professional':
            days_valid = 365
            max_devices = 5
        
        # 准备激活数据
        activation_data = {
            "email": email,
            "product_type": product_type,
            "days_valid": days_valid,
            "generated_at": datetime.now().isoformat(),
            "valid_until": (datetime.now() + timedelta(days=days_valid)).isoformat(),
            "max_devices": max_devices,
            "purchase_id": purchase_id,
            "product_name": product_name,
            "version": "2.0"
        }
        
        # 生成校验码
        checksum = hashlib.md5(
            f"{email}:{product_type}:{days_valid}:{purchase_id}".encode()
        ).hexdigest()[:8]
        activation_data['checksum'] = checksum
        
        # 加密
        data_str = json.dumps(activation_data, separators=(',', ':'))
        encrypted = cipher.encrypt(data_str.encode())
        
        # Base64编码
        activation_code = base64.urlsafe_b64encode(encrypted).decode()
        
        # 格式化为易读格式 (8位一组)
        formatted_code = '-'.join([
            activation_code[i:i+8] 
            for i in range(0, min(len(activation_code), 48), 8)
        ])
        
        # 确保不超过59字符
        if len(formatted_code) > 59:
            formatted_code = formatted_code[:59]
        
        logger.info(f"🔐 生成专业激活码: {formatted_code[:20]}...")
        return formatted_code, activation_data
        
    except Exception as e:
        logger.error(f"❌ 生成专业激活码失败: {e}")
        # 降级到简单激活码
        return generate_simple_activation_code(email, product_type)

def generate_simple_activation_code(email, product_type="personal"):
    """生成简单的激活码"""
    import secrets
    
    # 生成随机部分
    random_part = secrets.token_hex(6).upper()
    
    # 产品类型代码
    type_codes = {
        'personal': 'P', 
        'professional': 'R',
        'business': 'B', 
        'enterprise': 'E'
    }
    type_code = type_codes.get(product_type, 'P')
    
    # 邮箱哈希
    email_hash = hashlib.md5(email.encode()).hexdigest()[:4].upper()
    
    # 时间戳（月日）
    timestamp = datetime.now().strftime('%m%d')
    
    # 组合激活码
    activation_code = f"PDF-{type_code}{timestamp}-{email_hash}-{random_part[:4]}-{random_part[4:8]}"
    
    # 计算有效期
    days_valid = 365
    max_devices = 3
    
    if product_type == 'professional':
        max_devices = 5
    elif product_type == 'business':
        days_valid = 365 * 2
        max_devices = 10
    elif product_type == 'enterprise':
        days_valid = 365 * 3
        max_devices = 99
    
    # 激活数据
    activation_data = {
        "email": email,
        "product_type": product_type,
        "generated_at": datetime.now().isoformat(),
        "valid_until": (datetime.now() + timedelta(days=days_valid)).isoformat(),
        "max_devices": max_devices,
        "days_valid": days_valid,
        "activation_code": activation_code
    }
    
    return activation_code, activation_data

def send_activation_email(email, activation_code, activation_data):
    """发送激活邮件"""
    
    # 检查邮件配置
    if not all([config.SMTP_HOST, config.SMTP_USER, config.SMTP_PASSWORD]):
        logger.error("❌ 邮件服务未配置，无法发送激活邮件")
        logger.info(f"📧 [模拟发送] 激活邮件到: {email}")
        logger.info(f"   🔑 激活码: {activation_code}")
        logger.info(f"   📅 有效期至: {activation_data.get('valid_until', 'N/A')}")
        return False
    
    try:
        # 从激活数据中提取信息
        product_type = activation_data.get('product_type', 'personal').capitalize()
        valid_until = activation_data.get('valid_until', '')[:10]
        max_devices = activation_data.get('max_devices', 3)
        product_name = activation_data.get('product_name', 'PDF Fusion Pro')
        
        # 创建邮件
        msg = MIMEMultipart('alternative')
        
        # 邮件头
        subject = f"🎉 您的 {product_name} {product_type} 版激活码"
        msg['Subject'] = subject
        msg['From'] = f"PDF Fusion Pro Team <{config.SMTP_USER}>"
        msg['To'] = email
        msg['Date'] = formatdate(localtime=True)
        
        # HTML 邮件内容
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{product_name} 激活码</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; color: white; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: white; padding: 30px; border-radius: 0 0 10px 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .code {{ background: #f8f9fa; border: 2px dashed #667eea; padding: 20px; text-align: center; font-family: monospace; font-size: 18px; letter-spacing: 2px; margin: 20px 0; border-radius: 5px; word-break: break-all; }}
                .info {{ background: #e7f3ff; border-left: 4px solid #1890ff; padding: 15px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .footer {{ text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; color: #666; font-size: 12px; }}
                table {{ width: 100%; border-collapse: collapse; }}
                td {{ padding: 8px 0; border-bottom: 1px solid #eee; }}
                td:first-child {{ font-weight: bold; width: 100px; color: #555; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1 style="margin: 0; font-size: 28px;">🎉 感谢您购买 {product_name}！</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">您的 {product_type} 版激活信息</p>
            </div>
            
            <div class="content">
                <h2 style="color: #2c3e50; margin-top: 0;">📋 激活信息</h2>
                
                <table>
                    <tr>
                        <td>邮箱地址</td>
                        <td>{email}</td>
                    </tr>
                    <tr>
                        <td>产品版本</td>
                        <td>{product_type} 版</td>
                    </tr>
                    <tr>
                        <td>有效期至</td>
                        <td>{valid_until}</td>
                    </tr>
                    <tr>
                        <td>支持设备</td>
                        <td>{max_devices} 台</td>
                    </tr>
                </table>
                
                <h3 style="color: #2c3e50; margin-top: 30px;">🔑 您的激活码</h3>
                <div class="code">
                    {activation_code}
                </div>
                <p style="text-align: center; color: #666; font-size: 14px;">
                    请复制此激活码，在软件激活窗口中粘贴使用
                </p>
                
                <div class="info">
                    <h4 style="margin-top: 0; color: #1890ff;">🚀 激活步骤</h4>
                    <ol>
                        <li>下载并安装 {product_name}</li>
                        <li>运行软件，点击"激活"按钮</li>
                        <li>粘贴上面的激活码</li>
                        <li>点击"激活"完成注册</li>
                    </ol>
                </div>
                
                <div class="warning">
                    <h4 style="margin-top: 0; color: #856404;">⚠️ 重要提醒</h4>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>每个激活码最多可在 <strong>{max_devices} 台设备</strong> 同时使用</li>
                        <li>请妥善保管此激活码，一旦丢失无法找回</li>
                        <li>如需更换设备，请先在原设备注销</li>
                        <li>技术支持邮箱：support@example.com</li>
                    </ul>
                </div>
            </div>
            
            <div class="footer">
                <p>© {datetime.now().year} {product_name}. 版权所有。</p>
                <p>此邮件为系统自动发送，请勿直接回复。</p>
            </div>
        </body>
        </html>
        """
        
        # 纯文本内容（备用）
        text_content = f"""
感谢您购买 {product_name}！

您的激活信息：
邮箱地址：{email}
产品版本：{product_type}版
有效期至：{valid_until}
支持设备：{max_devices}台

您的激活码：{activation_code}

激活步骤：
1. 下载并安装 {product_name}
2. 运行软件，点击"激活"按钮
3. 粘贴上面的激活码
4. 点击"激活"完成注册

重要提醒：
• 每个激活码最多可在 {max_devices} 台设备同时使用
• 请妥善保管此激活码，一旦丢失无法找回
• 如需更换设备，请先在原设备注销
• 技术支持邮箱：support@example.com

© {datetime.now().year} {product_name}. 版权所有。
此邮件为系统自动发送，请勿直接回复。
        """
        
        # 添加文本和HTML版本
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))
        
        # 连接SMTP服务器并发送
        logger.info(f"📤 正在发送邮件到: {email}")
        
        with smtplib.SMTP(config.SMTP_HOST, int(config.SMTP_PORT)) as server:
            server.starttls()  # 启用安全连接
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"✅ 激活邮件已成功发送到: {email}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 发送邮件失败: {e}")
        # 记录模拟发送信息以便调试
        logger.info(f"📧 [失败模拟] 激活邮件到: {email}")
        logger.info(f"   🔑 激活码: {activation_code}")
        logger.info(f"   📅 有效期至: {activation_data.get('valid_until', 'N/A')}")
        return False

def save_activation_record(email, activation_code, activation_data):
    """保存激活记录到数据库或文件"""
    try:
        if config.DATABASE_URL:
            # 尝试保存到数据库
            return save_to_database(email, activation_code, activation_data)
        else:
            # 保存到本地文件
            return save_to_file(email, activation_code, activation_data)
    except Exception as e:
        logger.error(f"保存记录失败: {e}")
        # 尝试文件备份
        return save_to_file(email, activation_code, activation_data)

def save_to_database(email, activation_code, activation_data):
    """保存到数据库"""
    try:
        import psycopg2
        import psycopg2.extras
        
        conn = psycopg2.connect(config.DATABASE_URL)
        cursor = conn.cursor()
        
        cursor.execute('''
        INSERT INTO activations 
        (email, activation_code, product_type, days_valid, max_devices, valid_until, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (activation_code) DO NOTHING
        ''', (
            email,
            activation_code,
            activation_data['product_type'],
            activation_data['days_valid'],
            activation_data['max_devices'],
            activation_data['valid_until'],
            json.dumps(activation_data)
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"💾 激活码保存到数据库: {activation_code[:20]}...")
        return True
        
    except ImportError:
        logger.warning("未安装 psycopg2，降级到文件存储")
        return save_to_file(email, activation_code, activation_data)
    except Exception as e:
        logger.error(f"数据库保存失败: {e}")
        return save_to_file(email, activation_code, activation_data)

def save_to_file(email, activation_code, activation_data):
    """保存到本地文件"""
    try:
        import csv
        from datetime import datetime
        
        filename = "activations.csv"
        file_exists = os.path.exists(filename)
        
        with open(filename, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['时间', '邮箱', '激活码', '产品类型', '有效期至', '最大设备数'])
            
            writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                email,
                activation_code,
                activation_data['product_type'],
                activation_data['valid_until'][:10],
                activation_data['max_devices']
            ])
        
        logger.info(f"📄 激活码保存到文件: {activation_code}")
        return True
        
    except Exception as e:
        logger.error(f"文件保存失败: {e}")
        return False

# ==================== API 路由 ====================

@app.route('/')
def home():
    """主页"""
    storage_type = "数据库" if config.DATABASE_URL else "文件"
    
    return jsonify({
        "service": "PDF Fusion Pro 激活服务器",
        "version": "2.0.0",
        "status": "运行中",
        "timestamp": datetime.now().isoformat(),
        "storage": storage_type,
        "email_configured": smtp_configured,
        "encryption_configured": cipher is not None,
        "endpoints": {
            "health": "/health",
            "generate": "/api/generate",
            "verify": "/api/verify",
            "webhook": "/api/webhook/gumroad",
            "manual_activate": "/api/manual-activate",
            "debug_webhook": "/api/debug/webhook"
        }
    })

@app.route('/health')
def health_check():
    """健康检查"""
    try:
        # 测试数据库连接（如果配置了）
        db_status = "未配置"
        if config.DATABASE_URL:
            try:
                import psycopg2
                conn = psycopg2.connect(config.DATABASE_URL)
                conn.close()
                db_status = "连接正常"
            except:
                db_status = "连接失败"
        
        # 邮件服务状态
        email_status = "未配置"
        if smtp_configured:
            email_status = "已配置"
        
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": db_status,
            "email_service": email_status,
            "encryption": "已启用" if cipher else "未启用",
            "version": "2.0.0"
        })
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/api/generate', methods=['POST'])
@require_api_key
def api_generate():
    """生成激活码"""
    try:
        data = request.json
        
        # 验证输入
        email = data.get('email')
        if not email:
            return jsonify({"error": "邮箱地址是必需的"}), 400
        
        product_type = data.get('product_type', 'personal')
        days = data.get('days', 365)
        
        # 生成激活码
        activation_code, activation_data = generate_simple_activation_code(email, product_type)
        
        # 保存记录
        save_activation_record(email, activation_code, activation_data)
        
        logger.info(f"✅ 生成激活码: {email} -> {activation_code}")
        
        return jsonify({
            "success": True,
            "message": "激活码生成成功",
            "activation_code": activation_code,
            "data": activation_data
        })
        
    except Exception as e:
        logger.error(f"生成激活码失败: {e}")
        return jsonify({"error": "服务器错误"}), 500

@app.route('/api/verify', methods=['POST'])
def api_verify():
    """验证激活码"""
    try:
        data = request.json
        
        # 验证输入
        activation_code = data.get('activation_code')
        device_id = data.get('device_id', 'unknown')
        device_name = data.get('device_name', 'Unknown Device')
        
        if not activation_code:
            return jsonify({"error": "激活码是必需的"}), 400
        
        # 基本格式验证
        if not activation_code.startswith("PDF-"):
            return jsonify({
                "valid": False,
                "message": "无效的激活码格式"
            })
        
        # 提取产品类型
        product_type = 'personal'
        if len(activation_code) > 4:
            code_char = activation_code[4]
            if code_char == 'B':
                product_type = 'business'
            elif code_char == 'E':
                product_type = 'enterprise'
        
        # 模拟验证结果
        max_devices = 3 if product_type == "personal" else 10
        
        logger.info(f"✅ 验证激活码: {activation_code} -> {device_id}")
        
        return jsonify({
            "valid": True,
            "message": "激活成功",
            "data": {
                "product_type": product_type,
                "max_devices": max_devices,
                "valid_until": (datetime.now() + timedelta(days=365)).isoformat(),
                "device_id": device_id,
                "device_name": device_name
            }
        })
        
    except Exception as e:
        logger.error(f"验证激活码失败: {e}")
        return jsonify({"error": "服务器错误"}), 500

# ==================== 核心修复：Gumroad Webhook 处理 ====================
@app.route('/api/webhook/gumroad', methods=['POST'])
def webhook_gumroad():
    """处理Gumroad Webhook - 支持 form-urlencoded 格式"""
    try:
        logger.info("=" * 60)
        logger.info("📨 🎯 收到 Gumroad Webhook 请求")
        logger.info(f"📋 Content-Type: {request.content_type}")
        logger.info(f"📤 用户代理: {request.user_agent}")
        
        # 获取原始数据
        raw_data = request.get_data(as_text=True)
        logger.info(f"📄 原始数据长度: {len(raw_data)} 字符")
        
        # 解析数据
        data = {}
        
        if request.content_type == 'application/x-www-form-urlencoded':
            logger.info("🔄 解析 form-urlencoded 格式")
            data = parse_form_data(raw_data)
        elif request.content_type == 'application/json':
            logger.info("🔄 解析 JSON 格式")
            data = request.json
        else:
            # 尝试自动检测
            try:
                data = request.json
                logger.info("✅ 自动解析为 JSON")
            except:
                try:
                    data = parse_form_data(raw_data)
                    logger.info("✅ 自动解析为 form-urlencoded")
                except Exception as e:
                    logger.error(f"❌ 无法解析数据: {e}")
                    return jsonify({
                        "error": f"无法解析请求数据，Content-Type: {request.content_type}",
                        "supported_types": ["application/json", "application/x-www-form-urlencoded"]
                    }), 400
        
        if not data:
            logger.error("❌ 解析后数据为空")
            return jsonify({"error": "无法解析请求数据"}), 400
        
        # 日志数据内容
        logger.info(f"📊 解析后的数据字段: {list(data.keys())}")
        
        # 提取关键信息
        email = data.get('email')
        product_name = data.get('product_name', 'PDF Fusion Pro')
        sale_id = data.get('sale_id')
        order_number = data.get('order_number')
        
        logger.info(f"🔍 关键信息:")
        logger.info(f"   📧 Email: {email}")
        logger.info(f"   📦 Product: {product_name}")
        logger.info(f"   🆔 Sale ID: {sale_id}")
        logger.info(f"   🧾 Order: {order_number}")
        
        # 验证必要字段
        if not email:
            logger.error("❌ 缺少邮箱地址")
            return jsonify({"error": "邮箱地址缺失"}), 400
        
        # 确定产品类型
        product_name_lower = product_name.lower()
        product_type = 'personal'
        
        if 'business' in product_name_lower:
            product_type = 'business'
        elif 'enterprise' in product_name_lower:
            product_type = 'enterprise'
        elif 'professional' in product_name_lower:
            product_type = 'professional'
        
        logger.info(f"🏷️  产品类型: {product_type}")
        
        # 使用 sale_id 作为购买ID
        purchase_id = sale_id or order_number or f"gumroad_{int(datetime.now().timestamp())}"
        
        # 生成激活码
        logger.info(f"🔑 开始生成激活码...")
        activation_code, activation_data = generate_professional_activation_code(
            email=email,
            product_type=product_type,
            purchase_id=purchase_id,
            product_name=product_name
        )
        
        logger.info(f"✅ 激活码生成完成: {activation_code[:30]}...")
        
        # 保存激活记录
        save_success = save_activation_record(email, activation_code, activation_data)
        
        # 发送邮件
        email_sent = False
        if activation_code:
            logger.info(f"📤 准备发送邮件到: {email}")
            email_sent = send_activation_email(email, activation_code, activation_data)
        
        # 记录处理结果
        logger.info("=" * 60)
        logger.info(f"🎉 Gumroad Webhook 处理完成")
        logger.info(f"   📧 邮箱: {email}")
        logger.info(f"   🏷️  产品: {product_name}")
        logger.info(f"   🔑 激活码: {activation_code[:20]}...")
        logger.info(f"   📤 邮件状态: {'✅ 已发送' if email_sent else '❌ 发送失败'}")
        logger.info(f"   💾 保存状态: {'✅ 成功' if save_success else '❌ 失败'}")
        logger.info("=" * 60)
        
        return jsonify({
            "success": True,
            "message": "激活码已生成" + ("并发送" if email_sent else "（但邮件发送失败）"),
            "activation_code": activation_code,
            "email": email,
            "product_type": product_type,
            "email_sent": email_sent,
            "save_success": save_success
        })
        
    except Exception as e:
        logger.error(f"❌ Webhook处理失败: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/debug/webhook', methods=['POST'])
def debug_webhook():
    """调试Webhook - 显示原始数据"""
    try:
        logger.info("=" * 60)
        logger.info("🐛 调试 Webhook 请求")
        logger.info(f"📋 请求头: {dict(request.headers)}")
        
        raw_data = request.get_data(as_text=True)
        content_type = request.content_type
        
        result = {
            "method": request.method,
            "content_type": content_type,
            "raw_data": raw_data,
            "headers": dict(request.headers)
        }
        
        # 尝试解析
        if content_type == 'application/x-www-form-urlencoded':
            result['parsed_data'] = parse_form_data(raw_data)
        elif content_type == 'application/json':
            try:
                result['parsed_data'] = request.json
            except:
                result['parsed_data'] = "无法解析为JSON"
        else:
            result['parsed_data'] = "未知格式"
        
        logger.info(f"📊 解析结果: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}...")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"❌ 调试Webhook失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/manual-activate', methods=['POST'])
def manual_activate():
    """手动触发激活（用于测试和调试）"""
    try:
        logger.info("🛠️  收到手动激活请求")
        
        data = request.json
        
        # 验证必要字段
        required_fields = ['email', 'product_name']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return jsonify({
                "error": f"缺少必要字段: {', '.join(missing_fields)}",
                "required_fields": required_fields,
                "received_fields": list(data.keys())
            }), 400
        
        email = data['email']
        product_name = data['product_name']
        
        # 使用提供的购买ID或生成一个
        purchase_id = data.get('purchase_id', f"manual_{int(datetime.now().timestamp())}")
        
        # 判断产品类型
        product_name_lower = product_name.lower()
        product_type = 'personal'
        
        if 'business' in product_name_lower:
            product_type = 'business'
        elif 'enterprise' in product_name_lower:
            product_type = 'enterprise'
        elif 'professional' in product_name_lower:
            product_type = 'professional'
        
        logger.info(f"🛠️  手动激活参数:")
        logger.info(f"   📧 邮箱: {email}")
        logger.info(f"   🏷️  产品: {product_name} ({product_type})")
        logger.info(f"   🆔 购买ID: {purchase_id}")
        
        # 生成激活码
        activation_code, activation_data = generate_professional_activation_code(
            email=email,
            product_type=product_type,
            purchase_id=purchase_id,
            product_name=product_name
        )
        
        # 保存激活码
        save_success = save_activation_record(email, activation_code, activation_data)
        
        # 发送邮件
        email_sent = False
        if activation_code:
            email_sent = send_activation_email(email, activation_code, activation_data)
        
        return jsonify({
            "success": True,
            "message": "手动激活成功",
            "activation_code": activation_code,
            "email": email,
            "product_name": product_name,
            "product_type": product_type,
            "purchase_id": purchase_id,
            "email_sent": email_sent,
            "save_success": save_success,
            "note": "这是手动触发的激活"
        })
        
    except Exception as e:
        logger.error(f"❌ 手动激活失败: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/activations', methods=['GET'])
@require_api_key
def list_activations():
    """列出激活码"""
    try:
        activations = []
        
        if config.DATABASE_URL:
            # 从数据库读取
            try:
                import psycopg2
                import psycopg2.extras
                
                conn = psycopg2.connect(config.DATABASE_URL)
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                
                cursor.execute('''
                SELECT email, activation_code, product_type, generated_at 
                FROM activations 
                ORDER BY generated_at DESC 
                LIMIT 50
                ''')
                
                activations = cursor.fetchall()
                conn.close()
                
            except Exception as db_error:
                logger.error(f"数据库查询失败: {db_error}")
        
        # 如果数据库为空或失败，尝试从文件读取
        if not activations:
            try:
                import csv
                filename = "activations.csv"
                
                if os.path.exists(filename):
                    with open(filename, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        activations = list(reader)
            except Exception as file_error:
                logger.error(f"文件读取失败: {file_error}")
        
        return jsonify({
            "success": True,
            "count": len(activations),
            "activations": activations,
            "source": "database" if config.DATABASE_URL else "file"
        })
        
    except Exception as e:
        logger.error(f"列出激活码失败: {e}")
        return jsonify({"error": str(e)}), 500

# ==================== 错误处理 ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "未找到请求的资源"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "方法不允许"}), 405

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"服务器内部错误: {error}")
    return jsonify({"error": "服务器内部错误"}), 500

# ==================== 启动应用 ====================

# 初始化数据库
database_initialized = safe_init_database()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    
    logger.info("=" * 60)
    logger.info(f"🚀 启动 PDF Fusion Pro 激活服务器")
    logger.info(f"📅 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"🔑 管理员密钥: {config.ADMIN_API_KEY[:8]}...")
    logger.info(f"🔐 加密组件: {'已启用' if cipher else '未启用'}")
    logger.info(f"📧 邮件服务: {'已配置' if smtp_configured else '未配置'}")
    logger.info(f"💾 存储方式: {'数据库' if database_initialized else '文件'}")
    logger.info(f"🌐 服务端口: {port}")
    logger.info(f"🔗 Webhook地址: http://0.0.0.0:{port}/api/webhook/gumroad")
    logger.info("=" * 60)
    
    # 运行应用
    app.run(host='0.0.0.0', port=port, debug=False)
