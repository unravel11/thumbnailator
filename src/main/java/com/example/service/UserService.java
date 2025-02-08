package com.example.service;

import com.example.model.User;
import com.example.repository.UserRepository;
import com.example.util.ValidationUtil;
import com.example.util.LogUtil;

public class UserService {
    private UserRepository userRepository;
    private ValidationUtil validationUtil;
    private LogUtil logUtil;

    public User createUser(String name, String email) {
        logUtil.info("Creating new user: " + name);
        
        if (!validationUtil.validateEmail(email)) {
            logUtil.error("Invalid email: " + email);
            throw new IllegalArgumentException("Invalid email");
        }

        User user = new User(name, email);
        userRepository.save(user);
        logUtil.info("User created successfully: " + user.getId());
        return user;
    }

    public void updateUser(Long id, String name) {
        User user = userRepository.findById(id);
        user.setName(name);
        userRepository.save(user);
        logUtil.info("User updated: " + id);
    }
}