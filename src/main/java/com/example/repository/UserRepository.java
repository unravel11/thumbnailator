package com.example.repository;

import com.example.model.User;
import com.example.util.DatabaseUtil;

public class UserRepository {
    private DatabaseUtil dbUtil;

    public User findById(Long id) {
        return dbUtil.executeQuery("SELECT * FROM users WHERE id = " + id);
    }

    public void save(User user) {
        dbUtil.executeUpdate("INSERT INTO users ...");
    }
} 