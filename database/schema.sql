-- PDF Fusion Pro 数据库表结构

-- 激活码表
CREATE TABLE IF NOT EXISTS activations (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    activation_code TEXT NOT NULL UNIQUE,
    product_type VARCHAR(50) DEFAULT 'personal',
    days_valid INTEGER DEFAULT 365,
    max_devices INTEGER DEFAULT 3,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP NOT NULL,
    is_used BOOLEAN DEFAULT FALSE,
    used_at TIMESTAMP,
    used_by_device TEXT,
    purchase_id TEXT,
    order_id TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 设备激活表
CREATE TABLE IF NOT EXISTS device_activations (
    id SERIAL PRIMARY KEY,
    activation_id INTEGER REFERENCES activations(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    device_name TEXT,
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(activation_id, device_id)
);

-- 购买记录表
CREATE TABLE IF NOT EXISTS purchases (
    id SERIAL PRIMARY KEY,
    purchase_id TEXT UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    product_name TEXT,
    price DECIMAL(10, 2),
    currency VARCHAR(10),
    purchased_at TIMESTAMP,
    gumroad_data JSONB DEFAULT '{}',
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_activations_code ON activations(activation_code);
CREATE INDEX IF NOT EXISTS idx_activations_email ON activations(email);
CREATE INDEX IF NOT EXISTS idx_purchases_purchase_id ON purchases(purchase_id);
CREATE INDEX IF NOT EXISTS idx_device_activations ON device_activations(activation_id, device_id);